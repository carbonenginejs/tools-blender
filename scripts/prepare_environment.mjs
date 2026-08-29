/**
 * Prepares one EVE solar system's lighting for Blender: its nebula as an
 * equirectangular image, and its star's colour and intensity.
 *
 * Most of a quad surface's light comes from the environment, so a hull lit by a
 * flat colour reads as far too dark however correct its material is. This makes
 * the real thing available.
 *
 * `tools-core` already derives what is needed -- the nebula a system inherits
 * from its region, and the star's light colour from its blackbody temperature
 * and intensity from its luminosity -- so this fetches rather than computes:
 *
 *     GET /eve/{build}/map/systems/{id}?expand=all
 *
 * Two conversions are needed on top. Blender's Environment Texture node takes
 * equirectangular rather than a cube, and the DDS reader decodes only a cube's
 * first face, so each face is rebuilt as its own single-face DDS, decoded, and
 * resampled into a sphere.
 *
 * The `_refl` and `_blur` cubes beside the base one are Carbon's prefiltered
 * specular chain and its irradiance. Blender does its own prefiltering, so the
 * base cube is the one to hand over; the others are what a port of Carbon's
 * probe would have needed.
 *
 * Usage:
 *   node scripts/prepare_environment.mjs --system <id> --out <dir>
 *        [--build <id>] [--service <url>] [--size 1024]
 */

import { mkdirSync, writeFileSync } from "node:fs";
import { join } from "node:path";
import { CjsDdsFormat } from "../../runtime/src/resource/formats/dds/index.js";

/**
 * Encodes float RGB as a Radiance .hdr, which Blender loads as HDR.
 *
 * A PNG would clip the nebula to 0..1 and throw away exactly the bright
 * detail an environment exists to provide. Radiance RGBE is the simplest
 * format that keeps it: a shared exponent per pixel, written flat rather
 * than run-length encoded.
 */
function encodeRadiance(pixels, width, height)
{
    const header = Buffer.from(
        `#?RADIANCE
FORMAT=32-bit_rle_rgbe

-Y ${height} +X ${width}
`, "ascii");
    const body = Buffer.allocUnsafe(width * height * 4);

    for (let index = 0; index < width * height; index++)
    {
        const red = pixels[index * 3], green = pixels[index * 3 + 1], blue = pixels[index * 3 + 2];
        const peak = Math.max(red, green, blue);
        const out = index * 4;

        if (!(peak > 1e-32))
        {
            body[out] = body[out + 1] = body[out + 2] = body[out + 3] = 0;
            continue;
        }

        const exponent = Math.ceil(Math.log2(peak));
        const scale = 256 / Math.pow(2, exponent);
        body[out] = Math.min(255, Math.max(0, Math.floor(red * scale)));
        body[out + 1] = Math.min(255, Math.max(0, Math.floor(green * scale)));
        body[out + 2] = Math.min(255, Math.max(0, Math.floor(blue * scale)));
        body[out + 3] = exponent + 128;
    }

    return Buffer.concat([ header, body ]);
}


/** dwCaps2 sits at header offset 108, so file offset 112. 116 is dwCaps3, and
 *  clearing that leaves the cube flag set and the reader still wanting 6 faces. */
const CAPS2_OFFSET = 4 + 108;

function parseArguments(argv)
{
    const parsed = { system: "", out: "", build: "latest", service: "http://127.0.0.1:5510", size: 4096, face: 1024 };
    for (let index = 0; index < argv.length; index++)
    {
        switch (argv[index])
        {
            case "--system": parsed.system = argv[++index]; break;
            case "--out": parsed.out = argv[++index]; break;
            case "--build": parsed.build = argv[++index]; break;
            case "--service": parsed.service = argv[++index]; break;
            case "--size": parsed.size = Number(argv[++index]); break;
            case "--face": parsed.face = Number(argv[++index]); break;
            default: throw new Error(`Unknown argument "${argv[index]}"`);
        }
    }
    if (!parsed.system || !parsed.out) throw new Error("--system and --out are required");
    return parsed;
}

/** Decodes one face of a cube DDS by rebuilding it as a plain 2D DDS. */
/** Box-downsamples a face, so six 2048-square float faces need not be held at
 *  once -- that is 400MB, against 100MB at half. */
function downsample(pixels, width, height, target)
{
    if (!target || target >= width) return { pixels, width, height };
    const step = Math.floor(width / target);
    const out = new Float32Array(target * target * 4);

    for (let y = 0; y < target; y++)
    {
        for (let x = 0; x < target; x++)
        {
            let r = 0, g = 0, b = 0;
            for (let sy = 0; sy < step; sy++)
            {
                for (let sx = 0; sx < step; sx++)
                {
                    const o = (((y * step) + sy) * width + (x * step) + sx) * 4;
                    r += pixels[o]; g += pixels[o + 1]; b += pixels[o + 2];
                }
            }
            const count = step * step;
            const o = (y * target + x) * 4;
            out[o] = r / count; out[o + 1] = g / count; out[o + 2] = b / count; out[o + 3] = 1;
        }
    }
    return { pixels: out, width: target, height: target };
}


function decodeFaces(bytes, info, faceSize)
{
    const perFace = info.dataBytes / info.faces;
    return Array.from({ length: info.faces }, (_, index) =>
    {
        const single = new Uint8Array(info.dataOffset + perFace);
        single.set(bytes.subarray(0, info.dataOffset), 0);
        new DataView(single.buffer).setUint32(CAPS2_OFFSET, 0, true);
        single.set(
            bytes.subarray(info.dataOffset + index * perFace, info.dataOffset + (index + 1) * perFace),
            info.dataOffset
        );
        const decoded = CjsDdsFormat.read(single, { emit: "rgba" });
        // BC6H decodes to rgba32float, so the nebula's HDR survives; an 8-bit
        // emit would clip exactly the bright detail the environment is for.
        return downsample(decoded.data, decoded.width, decoded.height, faceSize);
    });
}

/** Standard cube lookup; DDS face order is +x, -x, +y, -y, +z, -z. */
function sampleCube(faces, x, y, z)
{
    const ax = Math.abs(x), ay = Math.abs(y), az = Math.abs(z);
    let index, u, v, major;

    if (ax >= ay && ax >= az)
    {
        major = ax;
        if (x > 0) { index = 0; u = -z; v = -y; } else { index = 1; u = z; v = -y; }
    }
    else if (ay >= az)
    {
        major = ay;
        if (y > 0) { index = 2; u = x; v = z; } else { index = 3; u = x; v = -z; }
    }
    else
    {
        major = az;
        if (z > 0) { index = 4; u = x; v = -y; } else { index = 5; u = -x; v = -y; }
    }

    const face = faces[index];
    const fx = Math.min(face.width - 1, Math.max(0, Math.floor((u / major * 0.5 + 0.5) * face.width)));
    const fy = Math.min(face.height - 1, Math.max(0, Math.floor((v / major * 0.5 + 0.5) * face.height)));
    const offset = (fy * face.width + fx) * 4;
    return [ face.pixels[offset], face.pixels[offset + 1], face.pixels[offset + 2] ];
}

function toEquirectangular(faces, width)
{
    const height = width >> 1;
    const out = new Float32Array(width * height * 3);

    for (let py = 0; py < height; py++)
    {
        // First row is the zenith, which is what the Radiance `-Y` header
        // declares and what Blender then expects.
        const elevation = (0.5 - (py + 0.5) / height) * Math.PI;
        const up = Math.sin(elevation), radius = Math.cos(elevation);

        for (let px = 0; px < width; px++)
        {
            const phi = ((px + 0.5) / width - 0.5) * 2 * Math.PI;
            // Blender is Z-up and EVE Y-up, so a Blender direction is built
            // first and then rotated into EVE's axes: (x, z, -y), a quarter
            // turn about X and nothing else.
            //
            // That matters. This previously read (-x, up, z), which is the
            // same turn with a MIRROR in it -- determinant -1 -- so the sky
            // came out upside down and backwards. A mirrored nebula looks
            // entirely plausible, which is why it went unnoticed; the
            // determinant is the only thing that catches it.
            //
            // Which way the sky FACES is genuinely a scene decision, and
            // stays one: it is a turn about the vertical, adjustable with a
            // Mapping node.
            const east = radius * Math.cos(phi), north = radius * Math.sin(phi);
            const [ red, green, blue ] = sampleCube(faces, east, up, -north);
            const offset = (py * width + px) * 3;
            out[offset] = red;
            out[offset + 1] = green;
            out[offset + 2] = blue;
        }
    }

    return { pixels: out, width, height };
}

const parsed = parseArguments(process.argv.slice(2));
mkdirSync(parsed.out, { recursive: true });

const base = `${parsed.service}/eve/${parsed.build}`;
const systemResponse = await fetch(`${base}/map/systems/${parsed.system}?expand=all`);
if (!systemResponse.ok) throw new Error(`system ${parsed.system}: HTTP ${systemResponse.status}`);
const system = await systemResponse.json();

const scene = system.derived?.scene ?? {};
const nebulaPath = scene.nebula?.graphics?.scene ?? "";
if (!nebulaPath) throw new Error(`${system.name}: no nebula scene`);

// The scene definition names a .black; the cube itself sits beside it, with
// _refl and _blur variants Carbon uses for its own prefiltering.
const cubePath = nebulaPath.replace(/\.black$/i, ".dds");
const cubeResponse = await fetch(`${base}/resources/${cubePath.replace(/^res:\//, "")}`);
if (!cubeResponse.ok) throw new Error(`${cubePath}: HTTP ${cubeResponse.status}`);
const cubeBytes = new Uint8Array(await cubeResponse.arrayBuffer());

const info = CjsDdsFormat.inspect(cubeBytes);
if (!info.isCube || !info.isCubeComplete) throw new Error(`${cubePath} is not a complete cube`);
console.log(`${system.name}: ${cubePath.split("/").pop()} ${info.width}x${info.height}, ${info.mipCount} mips`);

const image = toEquirectangular(decodeFaces(cubeBytes, info, parsed.face), parsed.size);
const environmentFile = join(parsed.out, "environment.hdr");
writeFileSync(environmentFile, encodeRadiance(image.pixels, image.width, image.height));

// The nebula's own EveSpaceScene carries the intensities the client applies.
// nebulaIntensity is why the skybox exceeds one on screen even though the cube
// itself tops out below it, and reflectionIntensity is the shader's cb2[14].w,
// scaling both environment samples.
let sceneSettings = {};
try
{
    const blackResponse = await fetch(`${base}/resources/${nebulaPath.replace(/^res:\//, "")}?format=json`);
    if (blackResponse.ok)
    {
        const black = await blackResponse.json();
        const object = black.object ?? {};
        sceneSettings = {
            nebulaIntensity: object.nebulaIntensity ?? 1,
            reflectionIntensity: object.reflectionIntensity ?? 1,
            ambientColor: object.ambientColor ?? null,
            envMap: object.envMapResPath ?? null,
            envMapBlur: object.envMap2ResPath ?? null
        };
        console.log(`  nebulaIntensity ${sceneSettings.nebulaIntensity}`
            + `, reflectionIntensity ${sceneSettings.reflectionIntensity}`);
    }
}
catch { /* the intensities are a refinement, not a requirement */ }

const sun = scene.sun ?? {};
const manifest = {
    schema: "carbon.blender-environment",
    version: 1,
    system: { id: system.id, name: system.name, securityStatus: system.securityStatus },
    nebula: { graphicID: scene.nebula?.graphicID, scene: nebulaPath, cube: cubePath },
    environment: "environment.hdr",
    sun: {
        color: sun.color ?? [ 1, 1, 1 ],
        intensity: sun.intensity ?? 1,
        // tools-core derives both: colour from the star's blackbody
        // temperature, intensity from its luminosity curve.
        derivedFrom: sun.derivedFrom ?? null,
        star: system.derived?.star?.spectralClass ?? null,
        // ccpwgl's EveSpaceScene default. GetPerFrameSunDirection negates and
        // normalises it, so this is the direction the light TRAVELS and the
        // shader's Sun.DirWorld is its negation. It is a scene property, not
        // something the nebula carries, so a caller may well want to set it.
        travel: [ 1, -1, 1 ]
    },
    scene: sceneSettings
};
writeFileSync(join(parsed.out, "environment.json"), JSON.stringify(manifest, null, 1));

console.log(`  sun ${manifest.sun.star ?? "?"} colour ${manifest.sun.color.map(c => c.toFixed(3)).join(", ")}`
    + ` intensity ${Number(manifest.sun.intensity).toFixed(3)}`);
console.log(`  wrote ${environmentFile} at ${image.width}x${image.height}`);
