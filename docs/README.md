# Blender tools documentation

Status: Evolving
Scope: CarbonEngineJS Blender add-on and bundled Python readers
Audience: Artists and developers importing EVE resources into Blender
Summary: Installation, supported sources, and the organization of imported scenes.

## Purpose

Build Blender scenes from EVE resource documents, with geometry importers,
shader approximations and separately configurable attachment lighting.

## Use this package when

Use the add-on to load ships by SOF DNA or import GR2 and CMF geometry. The
bundled format readers can also be used independently from Python.

## Where it fits

The hosted resource service supplies composed ship documents and resources.
The add-on projects those documents into Blender objects and materials; it
does not run Carbon's renderer or reproduce every shader feature exactly.

## Start here

Follow [setup](setup.md), then [build a ship](../README.md#build-a-ship-from-dna).
For lighting adjustments, see [attachment lighting](../README.md#adjust-attachment-lighting).

## Documentation map

- [Setup](setup.md): install the add-on and bundled readers.
- [Sources and Frontier](sources-and-frontier.md): choose a resource source.
- [Architecture](architecture.md): understand scene and parameter ownership.
- [Project overview](../README.md): capabilities, limitations and licensing.
