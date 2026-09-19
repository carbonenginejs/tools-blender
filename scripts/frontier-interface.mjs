/** Measure interfaces only; never store shader code or bytecode in Blender. */
import { createHash } from "node:crypto";
import { writeFile } from "node:fs/promises";
import { CjsHlslFormat } from "../../runtime/src/resource/formats/hlsl/index.js";

const [service = "http://127.0.0.1:5510", build = "3512930", output] = process.argv.slice(2);
const target = "frontier";
const families = [
    "quad/quadenvironmentv5", "pbr/structurematerial/structure",
    "pbr/asteroidmaterial/asteroid", "quad/quadv5", "quad/quadtriplanarv5",
    "pbr/simplepbr/simplepbr", "pbr/shipquadmaterial/ship", "quad/quaddetailv5",
    "fx/heat/fxheatv5", "fx/heat/fxheatdistortionv5", "organic/asteroidv5",
    "quad/quadsailsv5", "quad/quadheatv5", "fx/fxv5", "pbr/standardpbr/standardpbr",
    "pbr/turret/turret", "../../turret/v5/quadv5",
];
const tier = "sm_depth";
const sceneNames = new Set([
    "EveSpaceSceneEnvMap", "SSAOMap", "EveSpaceSceneShadowMap",
    "EveSpaceSceneDynamicShadowMap", "EveSpaceSceneReflectionCorrectionLookupTable",
    "LightIndexBuffer", "LightBuffer", "LightProfileArray", "ShadowMapAtlas",
    "SharedIndexVertexBuffer", "MorphTargetAnimations",
]);
const result = {schema: "carbon.quad-family-interface", version: 2, target, build,
    tier, permutation: {SPACE_OBJECT_PPT_ENABLED: "SOPPT_DISABLED"}, members: {}};

function constantsOf(stage) {
    const bytes = Uint8Array.from(stage.constantValues);
    const view = new DataView(bytes.buffer);
    return Object.fromEntries(stage.constants.map(c => {
        if (c.offset % 4 || c.size % 4 || c.offset + c.size > bytes.length) {
            throw new Error(`Invalid constant extent: ${c.name}`);
        }
        return [c.name, {vec4: Math.floor(c.offset / 16), offset: c.offset,
            size: c.size, dimension: c.dimension, elements: c.elements,
            default: Array.from({length: c.size / 4}, (_, i) => view.getFloat32(c.offset + i * 4, true))}];
    }));
}

for (const name of families) {
    const effectPath = name.startsWith("../../")
        ? `res:/graphics/effect/managed/space/${name.slice(6)}.fx`
        : `res:/graphics/effect/managed/space/spaceobject/v5/${name}.fx`;
    const compiledPath = effectPath.replace("/effect/", "/effect.dx11/").replace(/\.fx$/, `.${tier}`);
    const response = await fetch(`${service}/${target}/${build}/resources/${compiledPath.slice(5)}`);
    if (!response.ok) throw new Error(`${compiledPath}: HTTP ${response.status}`);
    const bytes = new Uint8Array(await response.arrayBuffer());
    const raw = CjsHlslFormat.read(bytes, {emit: "raw"});
    const transparency = raw.m_permutations.find(p => p.name === "SPACE_OBJECT_TRANSPARENCY");
    for (const variant of transparency?.options ?? [null]) {
        const selectedOptions = {};
        let permutationIndex = 0, multiplier = 1;
        for (const axis of raw.m_permutations) {
            let value = axis.options[axis.defaultOption];
            if (axis.name === "SPACE_OBJECT_PPT_ENABLED") value = "SOPPT_DISABLED";
            if (axis.name === "SPACE_OBJECT_TRANSPARENCY") value = variant;
            const index = axis.options.indexOf(value);
            if (index < 0) throw new Error(`Invalid ${axis.name}=${value}`);
            selectedOptions[axis.name] = value;
            permutationIndex += index * multiplier;
            multiplier *= axis.options.length;
        }
        const shader = raw.GetShaderByIndex(permutationIndex).toJSON();
        const rawPass = shader.effect.techniques.find(t => t.name === "Main").passes[0];
        const reflected = CjsHlslFormat.read(bytes, {permutation: Object.entries(selectedOptions).map(([name, value]) => ({name, value}))});
        const pass = reflected.effect.techniques.find(t => t.name === "Main").passes[0];
        const annotations = Object.fromEntries(reflected.effect.annotations.map(entry => [entry.name,
            Object.fromEntries(entry.annotations.map(a => [a.name, a[["boolValue", "intValue", "floatValue", "stringValue"][a.type]]]))]));
        const stages = {};
        for (const [index, stageName] of ["vertex", "pixel", "geometry", "hull", "domain", "compute"].entries()) {
            const stage = rawPass.stageInputs[index];
            if (!stage?.m_exists) continue;
            const reflection = pass.stageInputs.find(s => s?.stageType === index);
            if (!reflection) throw new Error(`Missing reflected ${stageName}`);
            stages[stageName] = {defaultBlockBytes: stage.m_constantValueSize,
                constants: constantsOf(stage), resources: reflection.resources.map(r => ({
                    name: r.name, registerIndex: r.registerIndex, type: r.type,
                    isSRGB: r.isSRGB, scene: sceneNames.has(r.name)})), samplers: reflection.samplers};
        }
        for (const resource of stages.pixel.resources) {
            annotations[resource.name] ??= {};
            annotations[resource.name].Tr2sRGB = resource.isSRGB;
        }
        result.members[`${name}|${variant ?? "default"}`] = {
            name: name.split("/").at(-1), target, build, effectPath, compiledPath, tier,
            sha256: createHash("sha256").update(bytes).digest("hex"), selectedOptions, permutationIndex,
            constantBufferBytes: Math.ceil(stages.pixel.defaultBlockBytes / 16) * 16,
            constants: stages.pixel.constants,
            textures: stages.pixel.resources.filter(r => !r.scene).map(r => r.name),
            sceneTextures: stages.pixel.resources.filter(r => r.scene).map(r => r.name),
            annotations, stages, evidence: "compiled-interface",
        };
        const entry = result.members[`${name}|${variant ?? "default"}`];
        // Sharing is earned by identical selected pixel programs, never by
        // stripping a prefix from an arbitrary effect filename.
        if (["quad/quadv5", "quad/quadenvironmentv5", "quad/quaddetailv5", "quad/quadsailsv5", "quad/quadheatv5"].includes(name)) {
            entry.aliases = {};
            const pixel = pass.stageInputs.find(s => s?.stageType === 1).bytecode.bytes;
            const hash = data => createHash("sha256").update(Uint8Array.from(data)).digest("hex");
            for (const prefix of ["skinned_", "unpackedskinned_", "unpacked_"]) {
                const alias = effectPath.replace(/([^/]+)$/, `${prefix}$1`);
                const compiled = alias.replace("/effect/", "/effect.dx11/").replace(/\.fx$/, ".sm_depth");
                const response = await fetch(`${service}/${target}/${build}/resources/${compiled.slice(5)}`);
                if (response.status === 404) continue;
                if (!response.ok) throw new Error(`${compiled}: ${response.status}`);
                const aliasBytes = new Uint8Array(await response.arrayBuffer());
                const aliasShader = CjsHlslFormat.read(aliasBytes, {permutation: Object.entries(selectedOptions).map(([name, value]) => ({name, value}))});
                const aliasPass = aliasShader.effect.techniques.find(t => t.name === "Main").passes[0];
                const aliasPixel = aliasPass.stageInputs.find(s => s?.stageType === 1).bytecode.bytes;
                const aliasVertex = aliasPass.stageInputs.find(s => s?.stageType === 0).bytecode.bytes;
                if (hash(pixel) === hash(aliasPixel)) entry.aliases[alias] = {
                    sha256: hash(aliasBytes), pixelSha256: hash(pixel), vertexSha256: hash(aliasVertex)};
            }
        }
    }
}
const json = JSON.stringify(result, null, 2) + "\n";
if (output) await writeFile(output, json); else process.stdout.write(json);
