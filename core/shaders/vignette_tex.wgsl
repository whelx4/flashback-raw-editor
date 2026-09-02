// Cosine vignette with a cool-edge tint, texture-resident.
//
// Twin of effects.apply_vignette (runs on linear ACEScg, pre-LUT). All channels
// share the same `dark` falloff; per-channel edge offsets push the periphery
// slightly cooler (red darkens a touch more, blue a touch less). Normalised
// coords match numpy linspace(-1, 1, n): pixel i -> -1 + 2i/(n-1).
//
//   r_norm  = clamp(length(xy) / sqrt(2), 0, 1)
//   falloff = pow(0.5*(1+cos(pi*r_norm)), feather)
//   dark    = 1 - strength*(1-falloff)
//   edge    = 1 - falloff
//   base = dark + edge * [-color_shift, 0, color_shift*.4]
//   tint = lerp([1,1,1], tint_rgb, edge)
//   out = max(0, in * base * tint)

struct U {
    strength:    f32,
    color_shift: f32,
    feather:     f32,
    _p:          f32,
    tint_rgb:    vec3f,
    _p2:         f32,
}

@group(0) @binding(0) var          src: texture_2d<f32>;
@group(0) @binding(1) var          dst: texture_storage_2d<rgba32float, write>;
@group(0) @binding(2) var<uniform> u:   U;

const PI: f32 = 3.14159265358979;
const INV_SQRT2: f32 = 0.70710678118655;

@compute @workgroup_size(8, 8)
fn main(@builtin(global_invocation_id) gid: vec3u) {
    let dimsu = textureDimensions(src);
    if gid.x >= dimsu.x || gid.y >= dimsu.y { return; }
    let dims = vec2f(dimsu);
    let denom = max(dims - vec2f(1.0), vec2f(1.0));
    let xy = vec2f(f32(gid.x), f32(gid.y)) / denom * 2.0 - vec2f(1.0);

    let r_norm  = clamp(length(xy) * INV_SQRT2, 0.0, 1.0);
    // base is the cosine falloff in [0,1]; at the exact corners f32 cos(pi) can
    // round just past -1, making base slightly negative -> pow(neg, frac) = NaN
    // -> max(0, NaN) = 0 -> black corners. Guard with select so base<=0 -> 0.
    let base    = 0.5 * (1.0 + cos(PI * r_norm));
    let falloff = select(pow(max(base, 0.0), u.feather), 0.0, base <= 0.0);
    let dark    = 1.0 - u.strength * (1.0 - falloff);
    let edge    = 1.0 - falloff;

    let pi = vec2i(i32(gid.x), i32(gid.y));
    let c = textureLoad(src, pi, 0).rgb;
    let legacy_delta = vec3f(-u.color_shift, 0.0, u.color_shift * 0.4);
    let base_factors = vec3f(dark) + edge * legacy_delta;
    let factors = base_factors * (vec3f(1.0) + edge * (u.tint_rgb - vec3f(1.0)));
    let outc = max(vec3f(0.0), c * factors);
    textureStore(dst, pi, vec4f(outc, 1.0));
}
