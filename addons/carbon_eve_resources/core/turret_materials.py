"""Carbon's turret parameter lookup, before Blender material construction.

Follows `SetupTurretMaterialFromDNA` for every source. Material and pattern
prefixes and `turretAreaType` come from the source's generic data (Frontier
uses its turret area, 10; EVE's generic data omits it, so primary, 0), and
material slots are remapped through the ship faction's `materialUsageList`.
Each parameter tries the DNA's material override, then pattern layers, then
race values (primary and reactor areas only), then the faction's area
materials and named glow colours, falling back to the primary area. Every
effect parameter is looked up, so there is no fixed list of colour fields;
unmatched ones keep the turret resource's defaults.
"""
from copy import deepcopy
from .sof_resolution import parse


def parameters(effect):
    """SetupTurretMaterialFromDNA uses constants, otherwise vector parameters."""
    constants = effect.get("constParameters") or []
    if not isinstance(constants, list):
        raise ValueError("The service must decode turret constants as named values")
    if constants:
        return constants
    return [row for row in effect.get("parameters", [])
            if row.get("_type", "Tr2Vector4Parameter") == "Tr2Vector4Parameter"
            and isinstance(row.get("value"), (list, tuple)) and len(row["value"]) == 4]


def resolve(effect, generic, faction, material, *, race=None, dna="", sof6=False):
    """EveSOFDNA::GetFactionTurretParameters/GetMeshAreaParameter.

    Source: EveSOFDNA.cpp:1254-1382, EveSOFUtils.cpp:172-216.
    Retain all four lanes. Missing lookups leave the resource value unchanged.
    """
    prefixes = [row["str"] if isinstance(row, dict) else row
                for row in generic.get("materialPrefixes", [])]
    pattern_prefixes = [row["str"] if isinstance(row, dict) else row
                        for row in generic.get("patternMaterialPrefixes", [])]
    area = int(generic.get("turretAreaType", 0))
    usage = faction.get("materialUsageList") or list(range(len(prefixes)))
    colors = (faction.get("colorData") or {}).get("colors") or []
    parsed = parse(dna) if dna else None

    def split(name, choices):
        for index, prefix in enumerate(choices):
            if name.casefold().startswith(prefix.casefold()):
                return index, name[len(prefix):]
        return None, name

    def value(material_name, short):
        if not material_name or material_name == "none":
            return None
        return (material(material_name).get("parameters") or {}).get(short)

    def area_value(record, area_type, index, short, full):
        table = (record or {}).get("areaMaterials") or {}
        names = table.get("materialNames") or {}
        glows = table.get("glowColor") or {}
        key = f"{area_type}:{index if index is not None else full}"
        if index is not None and key in names:
            return value(names[key], short)
        if index is None and key in glows:
            color = int(glows[key])
            return colors[color] if 0 <= color < len(colors) else None
        if area_type != 0:
            return area_value(record, 0, index, short, full)
        return None

    found = {}
    for parameter in parameters(effect):
        name = parameter["name"]
        index, short = split(name, prefixes)
        full = name
        if index is not None:
            index = int(usage[index])
            full = prefixes[index] + short
        result = None
        if parsed and index is not None and index < len(parsed.materials):
            result = value(parsed.materials[index], short)
        pattern_index, pattern_short = split(full, pattern_prefixes)
        if result is None and pattern_index is not None:
            if parsed and pattern_index + 1 < len(parsed.pattern):
                result = value(parsed.pattern[pattern_index + 1], pattern_short)
            # Carbon gates only the second default layer on UsingSof6();
            # explicit DNA pattern materials apply independently of this flag.
            if result is None and (pattern_index == 0 or sof6):
                result = value(faction.get(f"defaultPatternLayer{pattern_index + 1}MaterialName"), pattern_short)
        if result is None and area in (0, 3):
            result = area_value(race, area, index, short, full)
        if result is None:
            result = area_value(faction, area, index, short, full)
        if result is not None:
            found[name] = list(result)
    return found


def apply(document, values):
    """Build a private effect with resolved constants; never modify cached input."""
    result = deepcopy(document)
    effect = result.get("turretEffect") or {}
    rows = parameters(effect)
    effect["constParameters"] = [dict(row, value=list(values.get(row["name"], row["value"])))
                                  for row in rows]
    return result
