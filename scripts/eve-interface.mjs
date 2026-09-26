/** Measure interfaces only; never store shader code or bytecode in Blender.
 *
 * EVE's family.json, generated through tools-core at a pinned build, in the
 * shape it has always had: permutationIndex, constantBufferBytes, textures,
 * sceneTextures, constants {vec4, default}, annotations - `sm_depth` at
 * SOPPT_ENABLED. Members keep the order below, which is the file's.
 *
 *   node scripts/eve-interface.mjs [service] [build] [output]
 */
import { writeFile } from "node:fs/promises";
import { CjsHlslFormat } from "../../runtime/src/resource/formats/hlsl/index.js";

const [service = "http://127.0.0.1:5510", build = "3478781", output] = process.argv.slice(2);
const target = "eve";
// Relative to managed/space/spaceobject/. The attachment-set effects
// (EvePlaneSet, EveSpotlightSet) live in fx/ beside v5/, not under it.
const families = [
    "v5/quad/quaddetailv5", "v5/quad/quadenvironmentv5", "v5/quad/quadglassv5", "v5/quad/quadheatdetailv5",
    "v5/quad/quadheatv5", "v5/quad/quadinstancedv5", "v5/quad/quadoilv5", "v5/quad/quadsailsv5", "v5/quad/quadv5",
    "v5/quad/quadwreckv5", "v5/fx/fxv5", "v5/fx/fxdistortionv5",
    "fx/planeglow", "fx/spotlightconepool", "fx/spotlightglowpool",
];
const tier = "sm_depth";
const sceneNames = new Set([
    "EveSpaceSceneEnvMap", "SSAOMap", "EveSpaceSceneShadowMap", "EveSceneFogVolumeMap", "DepthMap",
    "EveSpaceSceneDynamicShadowMap", "EveSpaceSceneReflectionCorrectionLookupTable",
    "LightIndexBuffer", "LightBuffer", "LightProfileArray", "ShadowMapAtlas",
    "SharedIndexVertexBuffer", "MorphTargetAnimations",
]);
const result = {schema: "carbon.quad-family-interface", version: 2, build, tier,
    permutation: {SPACE_OBJECT_PPT_ENABLED: "SOPPT_ENABLED"}, members: {}};

function constantsOf(stage) {
    const bytes = Uint8Array.from(stage.constantValues);
    const view = new DataView(bytes.buffer);
    return Object.fromEntries(stage.constants.map(c => {
        if (c.offset % 4 || c.size % 4 || c.offset + c.size > bytes.length) {
            throw new Error(`Invalid constant extent: ${c.name}`);
        }
        return [c.name, {vec4: Math.floor(c.offset / 16),
            default: Array.from({length: c.size / 4}, (_, i) => view.getFloat32(c.offset + i * 4, true))}];
    }));
}

for (const name of families) {
    const compiledPath = `res:/graphics/effect.dx11/managed/space/spaceobject/${name}.${tier}`;
    const response = await fetch(`${service}/${target}/${build}/resources/${compiledPath.slice(5)}`);
    if (!response.ok) throw new Error(`${compiledPath}: HTTP ${response.status}`);
    const bytes = new Uint8Array(await response.arrayBuffer());
    const raw = CjsHlslFormat.read(bytes, {emit: "raw"});
    const selectedOptions = {};
    let permutationIndex = 0, multiplier = 1;
    for (const axis of raw.m_permutations) {
        let value = axis.options[axis.defaultOption];
        if (axis.name === "SPACE_OBJECT_PPT_ENABLED") value = "SOPPT_ENABLED";
        const index = axis.options.indexOf(value);
        if (index < 0) throw new Error(`Invalid ${axis.name}=${value}`);
        selectedOptions[axis.name] = value;
        permutationIndex += index * multiplier;
        multiplier *= axis.options.length;
    }
    const rawPass = raw.GetShaderByIndex(permutationIndex).toJSON()
        .effect.techniques.find(t => t.name === "Main").passes[0];
    const reflected = CjsHlslFormat.read(bytes, {permutation: Object.entries(selectedOptions).map(([name, value]) => ({name, value}))});
    const pass = reflected.effect.techniques.find(t => t.name === "Main").passes[0];
    const annotations = Object.fromEntries(reflected.effect.annotations.map(entry => [entry.name,
        Object.fromEntries(entry.annotations.map(a => [a.name, a[["boolValue", "intValue", "floatValue", "stringValue"][a.type]]]))]));
    const resources = pass.stageInputs.find(s => s?.stageType === 1).resources
        .map(r => ({name: r.name, isSRGB: r.isSRGB, scene: sceneNames.has(r.name)}));
    // family.json states sRGB only where it is true; absent reads as linear.
    for (const resource of resources.filter(r => r.isSRGB)) {
        annotations[resource.name] ??= {};
        annotations[resource.name].Tr2sRGB = true;
    }
    const stage = rawPass.stageInputs[1];
    result.members[name.split("/").at(-1)] = {
        permutationIndex,
        constantBufferBytes: Math.ceil(stage.m_constantValueSize / 16) * 16,
        textures: resources.filter(r => !r.scene).map(r => r.name),
        sceneTextures: resources.filter(r => r.scene).map(r => r.name),
        constants: constantsOf(stage),
        annotations: Object.fromEntries(Object.keys(annotations).sort().map(key => [key, annotations[key]])),
    };
}
const json = JSON.stringify(result, null, 1);
if (output) await writeFile(output, json); else process.stdout.write(json + "\n");
