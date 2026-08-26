/**
 * Converts a CUBE texture into an equirectangular PNG that Blender can sample.
 *
 * Blender has no cube-texture node: the only thing that takes a direction and
 * returns a colour is Environment Texture, which wants an equirectangular
 * image. So a cube has to be unwrapped before it can be used at all.
 *
 * The alpha channel is CARRIED, which is the whole point for
 * `decalholev5`: the hull-breach interior is stored in the cube's ALPHA, and a
 * converter that keeps only RGB throws the interior away.
 *
 * Usage:
 *   node prepare_cube_texture.mjs <cube.dds> <out.png> [--width 1024]
 *
 * Only uncompressed 32-bit cubes are handled, which is what the decal interiors
 * are; a compressed one throws rather than writing something wrong.
 */

import crypto from "node:crypto";
import fs from "node:fs";
import zlib from "node:zlib";

const HEADER = 128;
const CAPS2_CUBEMAP = 0x200;

function parseArguments(argv)
{
    const parsed = { input: "", output: "", width: 1024, alphaToRgb: true, axes: "identity" };
    const rest = [];
    for (let index = 0; index < argv.length; index++)
    {
        if (argv[index] === "--width") parsed.width = Number(argv[++index]);
        else if (argv[index] === "--keep-colour") parsed.alphaToRgb = false;
        else if (argv[index] === "--axes") parsed.axes = argv[++index];
        else rest.push(argv[index]);
    }
    [ parsed.input, parsed.output ] = rest;
    if (!parsed.input || !parsed.output)
    {
        throw new Error("usage: prepare_cube_texture.mjs <cube.dds> <out.png> [--width N]");
    }
    return parsed;
}

/** The six faces at mip 0, in the DDS order +x, -x, +y, -y, +z, -z. */
function readFaces(bytes)
{
    if (bytes.toString("ascii", 0, 4) !== "DDS ") throw new Error("not a DDS");
    const height = bytes.readUInt32LE(12);
    const width = bytes.readUInt32LE(16);
    const mips = Math.max(1, bytes.readUInt32LE(28));
    // 0x70 is dwCaps2, NOT 0x74 -- that one is dwCaps3 and reads as zero, which
    // makes a cube look like a plain 2D texture.
    if (!(bytes.readUInt32LE(112) & CAPS2_CUBEMAP)) throw new Error("not a cube map");
    if (bytes.readUInt32LE(84) !== 0) throw new Error("compressed cubes are not handled");

    let perFace = 0;
    for (let mip = 0; mip < mips; mip++)
    {
        perFace += Math.max(1, width >> mip) * Math.max(1, height >> mip) * 4;
    }

    return Array.from({ length: 6 }, (_, face) =>
    {
        const start = HEADER + face * perFace;
        return { width, height, pixels: bytes.subarray(start, start + width * height * 4) };
    });
}

/** Standard cube lookup: the major axis picks the face, the other two the texel. */
function sampleCube(faces, x, y, z)
{
    const absolute = [ Math.abs(x), Math.abs(y), Math.abs(z) ];
    const major = Math.max(...absolute);
    let index, u, v;
    if (major === absolute[0]) { index = x > 0 ? 0 : 1; u = x > 0 ? -z : z; v = -y; }
    else if (major === absolute[1]) { index = y > 0 ? 2 : 3; u = x; v = y > 0 ? z : -z; }
    else { index = z > 0 ? 4 : 5; u = z > 0 ? x : -x; v = -y; }

    const face = faces[index];
    const fx = Math.min(face.width - 1, Math.max(0, Math.floor((u / major * 0.5 + 0.5) * face.width)));
    const fy = Math.min(face.height - 1, Math.max(0, Math.floor((v / major * 0.5 + 0.5) * face.height)));
    const offset = (fy * face.width + fx) * 4;
    return [ face.pixels[offset], face.pixels[offset + 1], face.pixels[offset + 2], face.pixels[offset + 3] ];
}

/**
 * Unwraps to equirectangular in BLENDER's convention, so a direction handed to
 * an Environment Texture node lands on the texel this writes:
 *
 *     u = atan2(y, -x) / 2pi + 0.5      v = asin(z) / pi + 0.5
 *
 * Blender is Z-up where the cube is Y-up, so the cube's Y is fed Blender's Z.
 */
/** How a direction handed to Blender maps onto the cube's own axes.
 *
 * The cube is sampled in the space the SHADER works in, not in Blender's world,
 * so this is a convention to be established once and then left alone. `--axes`
 * exists to establish it against a render rather than by argument.
 */
const AXES = {
    identity: (x, y, z) => [ x, y, z ],
    "z-up": (x, y, z) => [ x, z, y ],
};

function toEquirectangular(faces, width, alphaToRgb, axes)
{
    const height = width >> 1;
    const out = Buffer.alloc(width * height * 4);
    for (let row = 0; row < height; row++)
    {
        const theta = (row + 0.5) / height * Math.PI;
        const z = Math.cos(theta);
        const radius = Math.sin(theta);
        for (let column = 0; column < width; column++)
        {
            const phi = ((column + 0.5) / width - 0.5) * 2 * Math.PI;
            const x = -radius * Math.cos(phi);
            const y = radius * Math.sin(phi);
            const [ red, green, blue, alpha ] = sampleCube(faces, ...axes(x, y, z));
            const offset = (row * width + column) * 4;
            // The alpha is ALSO written into RGB. Blender's Environment
            // Texture -- the only node that takes a direction -- has no alpha
            // output at all, so an interior that lives only in alpha would be
            // unreachable. decalholev5 reads nothing but that channel, so the
            // colour is free to carry it; the original alpha stays in alpha.
            out[offset] = alphaToRgb ? alpha : red;
            out[offset + 1] = alphaToRgb ? alpha : green;
            out[offset + 2] = alphaToRgb ? alpha : blue;
            out[offset + 3] = alpha;
        }
    }
    return { width, height, pixels: out };
}

function crc32(buffer)
{
    let crc = ~0;
    for (const byte of buffer)
    {
        crc ^= byte;
        for (let bit = 0; bit < 8; bit++) crc = (crc >>> 1) ^ (0xEDB88320 & -(crc & 1));
    }
    return ~crc >>> 0;
}

function chunk(type, data)
{
    const head = Buffer.alloc(8);
    head.writeUInt32BE(data.length, 0);
    head.write(type, 4, "ascii");
    const tail = Buffer.alloc(4);
    tail.writeUInt32BE(crc32(Buffer.concat([ head.subarray(4), data ])), 0);
    return Buffer.concat([ head, data, tail ]);
}

function encodePng({ width, height, pixels })
{
    const raw = Buffer.alloc((width * 4 + 1) * height);
    for (let row = 0; row < height; row++)
    {
        raw[row * (width * 4 + 1)] = 0;   // filter: none
        pixels.copy(raw, row * (width * 4 + 1) + 1, row * width * 4, (row + 1) * width * 4);
    }
    const header = Buffer.alloc(13);
    header.writeUInt32BE(width, 0);
    header.writeUInt32BE(height, 4);
    header[8] = 8;    // bit depth
    header[9] = 6;    // colour type: RGBA
    return Buffer.concat([
        Buffer.from([ 0x89, 0x50, 0x4E, 0x47, 0x0D, 0x0A, 0x1A, 0x0A ]),
        chunk("IHDR", header),
        chunk("IDAT", zlib.deflateSync(raw, { level: 9 })),
        chunk("IEND", Buffer.alloc(0)),
    ]);
}

const parsed = parseArguments(process.argv.slice(2));
const source = fs.readFileSync(parsed.input);
const faces = readFaces(source);
const image = toEquirectangular(faces, parsed.width, parsed.alphaToRgb, AXES[parsed.axes]);
fs.writeFileSync(parsed.output, encodePng(image));

/* A converted file goes STALE SILENTLY when EVE ships a new texture: the PNG
 * still loads, still looks plausible, and is simply no longer what the client
 * has. So every conversion records what it came from. A consumer can hash the
 * source again and know, rather than having to notice by eye. */
const sidecar = {
    tool: "prepare_cube_texture.mjs",
    generated: {
        width: image.width, height: image.height, layout: "equirectangular",
        alphaInRgb: parsed.alphaToRgb, axes: parsed.axes,
    },
    source: {
        path: parsed.input.split("\\").join("/"),
        bytes: source.length,
        sha256: crypto.createHash("sha256").update(source).digest("hex"),
    },
};
fs.writeFileSync(parsed.output.replace(/[.]png$/, ".source.json"),
    JSON.stringify(sidecar, null, 4) + String.fromCharCode(10));
console.log(`  ${parsed.input} -> ${parsed.output} (${image.width}x${image.height}, alpha carried)`);
console.log(`  source sha256 ${sidecar.source.sha256.slice(0, 16)}... recorded beside it`);
