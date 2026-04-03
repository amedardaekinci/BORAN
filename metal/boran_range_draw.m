/*
 * boran_range_draw.m — In-Process Metal Range Circle Overlay
 *
 * League of Legends'ın Metal render pipeline'ına 3 hook ile bağlanır:
 *   1. [CAMetalLayer nextDrawable]                      — drawable texture + device yakala
 *   2. [MTLCommandBuffer renderCommandEncoderWithDescriptor:] — final-pass encoder tag'le
 *   3. [MTLRenderCommandEncoder endEncoding]            — range circle çiz
 *
 * Oyunun kendi Metal context'inde çizer — doğru Z-order, pixel-perfect.
 * Shared memory gerektirmez — doğrudan game memory'den okur (in-process).
 *
 * Build:
 *   clang -dynamiclib -O2 -arch arm64 -fobjc-arc -fobjc-link-runtime \
 *         -framework Metal -framework QuartzCore -framework Foundation \
 *         -lpthread -o boran_range_draw.dylib boran_range_draw.m
 *
 * Kuki referans: /kuki/payload/kuki_metal_draw.m (613 satır)
 */

#import <Metal/Metal.h>
#import <QuartzCore/CAMetalLayer.h>
#import <Foundation/Foundation.h>
#import <objc/runtime.h>
#include <os/lock.h>
#include <mach-o/dyld.h>
#include <mach-o/loader.h>
#include <syslog.h>
#include <math.h>
#include <string.h>
#include <stdint.h>
#include <signal.h>
#include <setjmp.h>
#include <mach/mach.h>

/* ============================================================
 * Offsets — Boran projesinin keşfettiği değerler
 * GlobalPage: ADRP scan ile dinamik bulunur
 * ============================================================ */
#define OFF_HERO_MANAGER_FROM_GP  0x478ULL   /* GP + bu = HeroManager ptr */
#define OFF_HERO_ARRAY            0x8ULL     /* HeroMgr + bu = hero array ptr */
#define OFF_HERO_COUNT            0x10ULL    /* HeroMgr + bu = hero count */

/* Hero object field offset'leri (doğrulanmış) */
#define OFF_RENDER_POS  0x98ULL     /* hero + bu = vec3 render position */
#define OFF_HP          0x36FCULL   /* hero + bu = current HP (float) */
#define OFF_MAX_HP      0x3700ULL   /* hero + bu = max HP (float) */
#define OFF_ATTACK_RANGE 0xF54ULL   /* hero + bu = attack range (float) */

/* Camera/ViewProj — GlobalPage'den relative */
#define OFF_CAMERA      0xDC8ULL    /* GP + bu = camera ptr */
#define OFF_VIEWPROJ    0xECULL     /* camera + bu = ViewProj float[16] */

/* ============================================================
 * Logging
 * ============================================================ */
#define BLOG(fmt, ...) syslog(LOG_NOTICE, "[BORAN-MTL] " fmt, ##__VA_ARGS__)

/* ============================================================
 * Vertex format — 24 bytes: packed_float2 pos + packed_float4 color
 * ============================================================ */
typedef struct {
    float x, y;        /* Metal NDC: [-1,+1], +Y = screen top */
    float r, g, b, a;  /* RGBA color */
} boran_vertex_t;

#define MAX_VTXS 2048
#define CIRCLE_SEGS 64  /* Daha smooth circle için 64 segment */

/* ============================================================
 * MSL Shader Source
 * ============================================================ */
static const char kMSLSource[] =
    "#include <metal_stdlib>\n"
    "using namespace metal;\n"
    "struct Vtx { packed_float2 pos; packed_float4 color; };\n"
    "struct VOut { float4 pos [[position]]; float4 col; };\n"
    "vertex VOut vert_main(uint vid [[vertex_id]],\n"
    "                      constant Vtx *v [[buffer(0)]]) {\n"
    "    VOut o;\n"
    "    o.pos = float4(v[vid].pos.x, v[vid].pos.y, 0.0, 1.0);\n"
    "    o.col = float4(v[vid].color);\n"
    "    return o;\n"
    "}\n"
    "fragment float4 frag_main(VOut in [[stage_in]]) {\n"
    "    return in.col;\n"
    "}\n";

/* ============================================================
 * Global State
 * ============================================================ */
typedef struct {
    /* Metal pipeline */
    __strong id<MTLDevice>               device;
    __strong id<MTLRenderPipelineState>  pipeline;
    __strong id<MTLBuffer>               vtx_buf;
    MTLPixelFormat                       px_fmt;

    /* Per-frame */
    __weak   id<MTLTexture>              drawable_tex;
    __weak   id<MTLRenderCommandEncoder> tagged_enc;
    uint32_t                             draw_w;
    uint32_t                             draw_h;

    /* Game data — in-process, doğrudan memory oku */
    uint64_t game_base;
    uint64_t global_page;   /* ADRP scan ile bulunan GP adresi */

    os_unfair_lock lock;
    int            ready;
    int            gp_found;  /* GlobalPage bulundu mu? */
} boran_state_t;

static boran_state_t g_st;

/* ============================================================
 * Original IMP function pointers
 * ============================================================ */
typedef id<CAMetalDrawable>         (*fn_nextDrawable)(id, SEL);
typedef id<MTLRenderCommandEncoder> (*fn_makeEncoder)(id, SEL,
                                                      MTLRenderPassDescriptor *);
typedef void                        (*fn_endEncoding)(id, SEL);

static fn_nextDrawable orig_nextDrawable = NULL;
static fn_makeEncoder  orig_makeEncoder  = NULL;
static fn_endEncoding  orig_endEncoding  = NULL;

/* ============================================================
 * Game base bulma (in-process, dyld ile)
 * ============================================================ */
static uint64_t find_game_base(void) {
    uint32_t n = _dyld_image_count();
    for (uint32_t i = 0; i < n; i++) {
        const char *name = _dyld_get_image_name(i);
        if (name && strstr(name, "LeagueofLegends"))
            return (uint64_t)_dyld_get_image_header(i);
    }
    return n > 0 ? (uint64_t)_dyld_get_image_header(0) : 0;
}

/* ============================================================
 * Safe memory read — crash-proof via vm_read_overwrite
 * Kendi process'imizde bile unmapped page'lere erişebiliriz,
 * bu yüzden vm_read kullanarak güvenli okuma yapıyoruz.
 * ============================================================ */
static int safe_read(uint64_t addr, void *buf, size_t size) {
    vm_size_t out_size = 0;
    kern_return_t kr = vm_read_overwrite(
        mach_task_self(), (vm_address_t)addr, (vm_size_t)size,
        (vm_address_t)buf, &out_size);
    return (kr == KERN_SUCCESS && out_size == (vm_size_t)size);
}

static uint64_t safe_read_u64(uint64_t addr) {
    uint64_t val = 0;
    safe_read(addr, &val, 8);
    return val;
}

static uint32_t safe_read_u32(uint64_t addr) {
    uint32_t val = 0;
    safe_read(addr, &val, 4);
    return val;
}

static float safe_read_float(uint64_t addr) {
    float val = 0;
    safe_read(addr, &val, 4);
    return val;
}

/* ============================================================
 * GlobalPage Dinamik Keşif (in-process, safe read)
 *
 * Data segment'te HeroManager signature arayan brute-force scan.
 * vm_read_overwrite ile güvenli — unmapped page crash etmez.
 * ============================================================ */
static uint64_t find_global_page(uint64_t base) {
    BLOG("Scanning for GlobalPage (base=0x%llx)...", (unsigned long long)base);

    for (uint64_t off = 0x1F00000; off <= 0x2500000; off += 0x1000) {
        uint64_t page = base + off;

        for (uint64_t hm_off = 0x400; hm_off < 0x600; hm_off += 8) {
            uint64_t mgr_ptr = safe_read_u64(page + hm_off);
            if (mgr_ptr < 0x100000000ULL || mgr_ptr > 0x900000000000ULL)
                continue;

            uint64_t arr = safe_read_u64(mgr_ptr + OFF_HERO_ARRAY);
            uint32_t cnt = safe_read_u32(mgr_ptr + OFF_HERO_COUNT);

            if (arr < 0x100000000ULL || arr > 0x900000000000ULL)
                continue;
            if (cnt < 1 || cnt > 12)
                continue;

            uint64_t hero0 = safe_read_u64(arr);
            if (hero0 < 0x100000000ULL || hero0 > 0x900000000000ULL)
                continue;

            float range = safe_read_float(hero0 + OFF_ATTACK_RANGE);
            if (range >= 100.0f && range <= 700.0f) {
                BLOG("GlobalPage FOUND: 0x%llx (base+0x%llx), HeroMgr at +0x%llx, "
                     "count=%u, range=%.0f",
                     (unsigned long long)page,
                     (unsigned long long)off,
                     (unsigned long long)hm_off,
                     cnt, range);
                return page;
            }
        }
    }
    BLOG("GlobalPage NOT FOUND in scan range");
    return 0;
}

/* ============================================================
 * Hero verilerini oku (in-process, doğrudan memory)
 * ============================================================ */
typedef struct {
    float pos_x, pos_y, pos_z;  /* render position */
    float hp, max_hp;
    float attack_range;
} hero_data_t;

static int read_local_hero(hero_data_t *out) {
    if (!g_st.global_page) return 0;

    uint64_t mgr = safe_read_u64(g_st.global_page + OFF_HERO_MANAGER_FROM_GP);
    if (mgr < 0x100000000ULL) return 0;

    uint64_t arr = safe_read_u64(mgr + OFF_HERO_ARRAY);
    if (arr < 0x100000000ULL) return 0;

    uint64_t hero = safe_read_u64(arr);  /* hero[0] = local player */
    if (hero < 0x100000000ULL) return 0;

    /* Render position (vec3 at hero + 0x98) */
    float pos[3];
    if (!safe_read(hero + OFF_RENDER_POS, pos, 12)) return 0;
    out->pos_x = pos[0];
    out->pos_y = pos[1];
    out->pos_z = pos[2];

    out->hp           = safe_read_float(hero + OFF_HP);
    out->max_hp       = safe_read_float(hero + OFF_MAX_HP);
    out->attack_range = safe_read_float(hero + OFF_ATTACK_RANGE);

    return 1;
}

/* ============================================================
 * ViewProj matrix oku
 * ============================================================ */
static int read_viewproj(float vp[16]) {
    if (!g_st.global_page) return 0;
    uint64_t cam = safe_read_u64(g_st.global_page + OFF_CAMERA);
    if (cam < 0x100000000ULL || cam > 0x900000000000ULL) return 0;
    return safe_read(cam + OFF_VIEWPROJ, vp, 64);
}

/* ============================================================
 * World → Metal NDC projection
 * ============================================================ */
static int w2ndc(const float *mv,
                 float wx, float wy, float wz,
                 float *ox, float *oy) {
    float cx = mv[0]*wx + mv[4]*wy + mv[8]*wz  + mv[12];
    float cy = mv[1]*wx + mv[5]*wy + mv[9]*wz  + mv[13];
    float cw = mv[3]*wx + mv[7]*wy + mv[11]*wz + mv[15];
    if (cw < 0.001f) return 0;
    *ox = cx / cw;
    *oy = cy / cw;
    return (*ox > -1.1f && *ox < 1.1f && *oy > -1.1f && *oy < 1.1f);
}

/* ============================================================
 * Line emitter — thin quad (6 verts)
 * ============================================================ */
static int emit_line(boran_vertex_t *v, int n,
                     float x0, float y0, float x1, float y1, float width,
                     float r, float g, float b, float a) {
    if (n + 6 > MAX_VTXS) return n;
    float dx = x1 - x0, dy = y1 - y0;
    float len = sqrtf(dx*dx + dy*dy);
    if (len < 1e-6f) return n;
    float px = -dy / len * width * 0.5f;
    float py =  dx / len * width * 0.5f;
    v[n++] = (boran_vertex_t){x0+px, y0+py, r,g,b,a};
    v[n++] = (boran_vertex_t){x0-px, y0-py, r,g,b,a};
    v[n++] = (boran_vertex_t){x1+px, y1+py, r,g,b,a};
    v[n++] = (boran_vertex_t){x0-px, y0-py, r,g,b,a};
    v[n++] = (boran_vertex_t){x1-px, y1-py, r,g,b,a};
    v[n++] = (boran_vertex_t){x1+px, y1+py, r,g,b,a};
    return n;
}

/* ============================================================
 * Pipeline Build
 * ============================================================ */
static void build_pipeline_locked(MTLPixelFormat fmt) {
    NSError *err = nil;
    NSString *src = [NSString stringWithUTF8String:kMSLSource];

    id<MTLLibrary> lib = [g_st.device newLibraryWithSource:src
                                                   options:nil
                                                     error:&err];
    if (!lib) {
        BLOG("Shader compile error: %s", err.localizedDescription.UTF8String);
        return;
    }

    MTLRenderPipelineDescriptor *pd = [MTLRenderPipelineDescriptor new];
    pd.vertexFunction   = [lib newFunctionWithName:@"vert_main"];
    pd.fragmentFunction = [lib newFunctionWithName:@"frag_main"];
    pd.colorAttachments[0].pixelFormat = fmt;

    /* Alpha blending (src over) */
    pd.colorAttachments[0].blendingEnabled             = YES;
    pd.colorAttachments[0].sourceRGBBlendFactor        = MTLBlendFactorSourceAlpha;
    pd.colorAttachments[0].destinationRGBBlendFactor   = MTLBlendFactorOneMinusSourceAlpha;
    pd.colorAttachments[0].sourceAlphaBlendFactor      = MTLBlendFactorOne;
    pd.colorAttachments[0].destinationAlphaBlendFactor = MTLBlendFactorZero;

    pd.depthAttachmentPixelFormat   = MTLPixelFormatInvalid;
    pd.stencilAttachmentPixelFormat = MTLPixelFormatInvalid;

    g_st.pipeline = [g_st.device newRenderPipelineStateWithDescriptor:pd error:&err];
    if (!g_st.pipeline) {
        BLOG("Pipeline state error: %s", err.localizedDescription.UTF8String);
        return;
    }

    g_st.vtx_buf = [g_st.device
        newBufferWithLength:MAX_VTXS * sizeof(boran_vertex_t)
                   options:MTLResourceStorageModeShared];

    g_st.px_fmt = fmt;
    BLOG("Pipeline built (pixfmt=%lu)", (unsigned long)fmt);
}

/* ============================================================
 * Range Circle Çizimi — inject_range_circle
 *
 * Orijinal oyun range circle'ına benzer cyan/mavi renk.
 * 64 segment polyline, world space → NDC projected.
 * ============================================================ */
static void inject_range_circle(id<MTLRenderCommandEncoder> enc) {
    /* GlobalPage henüz bulunamadıysa tekrar dene */
    if (!g_st.gp_found) {
        g_st.global_page = find_global_page(g_st.game_base);
        if (g_st.global_page) {
            g_st.gp_found = 1;
        } else {
            return;  /* Henüz bulunamadı */
        }
    }

    /* Hero verilerini oku */
    hero_data_t hero;
    if (!read_local_hero(&hero)) return;
    if (hero.hp <= 0.0f) return;
    if (hero.attack_range < 50.0f || hero.attack_range > 2000.0f) return;

    /* ViewProj matrix */
    float vp[16];
    if (!read_viewproj(vp)) return;

    /* NDC line width: ~2.5px */
    float sw = (float)(g_st.draw_w > 0 ? g_st.draw_w : 1920);
    float sh = (float)(g_st.draw_h > 0 ? g_st.draw_h : 1080);
    float line_w = (2.5f / sw + 2.5f / sh) * 1.0f;

    boran_vertex_t *verts = (boran_vertex_t *)g_st.vtx_buf.contents;
    int n = 0;

    /* Range circle — orijinal oyundaki mavi-cyan renk
     * Oyundaki C tuşu range indicator'ı: açık mavi/cyan, hafif transparan */
    float prev_nx = 0.0f, prev_ny = 0.0f;
    int   prev_ok = 0;
    float range = hero.attack_range;

    for (int s = 0; s <= CIRCLE_SEGS; s++) {
        float angle = (float)s / (float)CIRCLE_SEGS * 6.28318530f;
        float wx = hero.pos_x + cosf(angle) * range;
        float wz = hero.pos_z + sinf(angle) * range;
        float cnx, cny;
        int ok = w2ndc(vp, wx, hero.pos_y + 5.0f, wz, &cnx, &cny);

        if (ok && prev_ok) {
            /* Orijinal cyan range circle rengi: RGB(0, 230, 230), alpha 0.8 */
            n = emit_line(verts, n,
                          prev_nx, prev_ny, cnx, cny,
                          line_w * 2.0f,
                          0.0f, 0.90f, 0.90f, 0.80f);
        }
        prev_nx = cnx;
        prev_ny = cny;
        prev_ok = ok;
    }

    if (n == 0) return;

    [enc setRenderPipelineState:g_st.pipeline];
    [enc setVertexBuffer:g_st.vtx_buf offset:0 atIndex:0];
    [enc drawPrimitives:MTLPrimitiveTypeTriangle
           vertexStart:0
           vertexCount:(NSUInteger)n];
}

/* ============================================================
 * Hook 1: CAMetalLayer nextDrawable
 * ============================================================ */
static id<CAMetalDrawable> hook_nextDrawable(id self, SEL _cmd) {
    id<CAMetalDrawable> drawable = orig_nextDrawable(self, _cmd);
    if (!drawable) return drawable;

    os_unfair_lock_lock(&g_st.lock);
    g_st.drawable_tex = drawable.texture;
    if (!g_st.device) {
        g_st.device = ((CAMetalLayer *)self).device;
        BLOG("MTL device: %s", g_st.device.name.UTF8String);
    }
    os_unfair_lock_unlock(&g_st.lock);

    return drawable;
}

/* ============================================================
 * Hook 2: MTLCommandBuffer renderCommandEncoderWithDescriptor:
 * ============================================================ */
static id<MTLRenderCommandEncoder>
hook_makeEncoder(id self, SEL _cmd, MTLRenderPassDescriptor *desc) {
    id<MTLRenderCommandEncoder> enc = orig_makeEncoder(self, _cmd, desc);
    if (!enc || !desc) return enc;

    id<MTLTexture> att = desc.colorAttachments[0].texture;

    os_unfair_lock_lock(&g_st.lock);
    if (att && att == g_st.drawable_tex) {
        g_st.tagged_enc = enc;
        g_st.draw_w = (uint32_t)att.width;
        g_st.draw_h = (uint32_t)att.height;

        MTLPixelFormat fmt = att.pixelFormat;
        if (!g_st.pipeline || g_st.px_fmt != fmt)
            build_pipeline_locked(fmt);
    }
    os_unfair_lock_unlock(&g_st.lock);

    return enc;
}

/* ============================================================
 * Hook 3: MTLRenderCommandEncoder endEncoding
 * ============================================================ */
static void hook_endEncoding(id self, SEL _cmd) {
    os_unfair_lock_lock(&g_st.lock);
    BOOL is_final = (self == (id)g_st.tagged_enc) && (g_st.pipeline != nil);
    if (is_final) g_st.tagged_enc = nil;
    os_unfair_lock_unlock(&g_st.lock);

    if (is_final)
        inject_range_circle((id<MTLRenderCommandEncoder>)self);

    orig_endEncoding(self, _cmd);
}

/* ============================================================
 * Hook Installation
 * ============================================================ */
static void install_hooks(void) {
    BLOG("Installing Metal hooks...");

    /* Hook 1: CAMetalLayer nextDrawable */
    {
        CAMetalLayer *probe = [CAMetalLayer layer];
        Class cls = object_getClass(probe);
        Method m  = class_getInstanceMethod(cls, @selector(nextDrawable));
        if (m) {
            orig_nextDrawable = (fn_nextDrawable)method_getImplementation(m);
            method_setImplementation(m, (IMP)hook_nextDrawable);
            BLOG("Hooked [%s nextDrawable]", class_getName(cls));
        } else {
            BLOG("ERROR: nextDrawable not found");
        }
    }

    /* Probe concrete Metal class names */
    id<MTLDevice> probe_dev = MTLCreateSystemDefaultDevice();
    if (!probe_dev) {
        BLOG("ERROR: MTLCreateSystemDefaultDevice failed");
        return;
    }

    id<MTLCommandQueue>  probe_q   = [probe_dev newCommandQueue];
    id<MTLCommandBuffer> probe_buf = [probe_q commandBuffer];

    MTLTextureDescriptor *td = [MTLTextureDescriptor
        texture2DDescriptorWithPixelFormat:MTLPixelFormatBGRA8Unorm
                                     width:1 height:1 mipmapped:NO];
    td.usage       = MTLTextureUsageRenderTarget;
    td.storageMode = MTLStorageModePrivate;
    id<MTLTexture> probe_tex = [probe_dev newTextureWithDescriptor:td];

    MTLRenderPassDescriptor *rpd = [MTLRenderPassDescriptor renderPassDescriptor];
    rpd.colorAttachments[0].texture     = probe_tex;
    rpd.colorAttachments[0].loadAction  = MTLLoadActionDontCare;
    rpd.colorAttachments[0].storeAction = MTLStoreActionDontCare;

    id<MTLRenderCommandEncoder> probe_enc =
        [probe_buf renderCommandEncoderWithDescriptor:rpd];

    Class cls_buf = object_getClass(probe_buf);
    Class cls_enc = object_getClass(probe_enc);
    BLOG("MTLCommandBuffer class: %s", class_getName(cls_buf));
    BLOG("MTLRenderCommandEncoder class: %s", class_getName(cls_enc));

    [probe_enc endEncoding];
    [probe_buf commit];

    /* Hook 2 */
    {
        SEL sel = @selector(renderCommandEncoderWithDescriptor:);
        Method m = class_getInstanceMethod(cls_buf, sel);
        if (m) {
            orig_makeEncoder = (fn_makeEncoder)method_getImplementation(m);
            method_setImplementation(m, (IMP)hook_makeEncoder);
            BLOG("Hooked [%s renderCommandEncoderWithDescriptor:]", class_getName(cls_buf));
        }
    }

    /* Hook 3 */
    {
        Method m = class_getInstanceMethod(cls_enc, @selector(endEncoding));
        if (m) {
            orig_endEncoding = (fn_endEncoding)method_getImplementation(m);
            method_setImplementation(m, (IMP)hook_endEncoding);
            BLOG("Hooked [%s endEncoding]", class_getName(cls_enc));
        }
    }

    g_st.ready = 1;
    BLOG("All hooks installed — range circle active");
}

/* ============================================================
 * Constructor / Destructor
 * ============================================================ */
__attribute__((constructor))
static void boran_metal_init(void) {
    BLOG("=== BORAN RANGE DRAW LOADING ===");

    g_st.lock      = OS_UNFAIR_LOCK_INIT;
    g_st.game_base = find_game_base();
    BLOG("Game base: 0x%llx", (unsigned long long)g_st.game_base);

    /* GlobalPage'i bul (başarısızsa her frame tekrar dener) */
    g_st.global_page = find_global_page(g_st.game_base);
    if (g_st.global_page) {
        g_st.gp_found = 1;
        BLOG("GlobalPage: 0x%llx", (unsigned long long)g_st.global_page);
    } else {
        BLOG("GlobalPage not found yet — will retry per frame");
    }

    dispatch_async(dispatch_get_main_queue(), ^{
        install_hooks();
    });
}

__attribute__((destructor))
static void boran_metal_cleanup(void) {
    BLOG("BORAN range draw unloading");
}
