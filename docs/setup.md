# Setting up the Blender add-on

Start to finish this is four clicks and no configuration. If you have used a
Blender add-on before, the [Install](#install) section is all you need.

## What you need

- **Blender 4.0 or newer.** Older versions will refuse to load the add-on.
- **An internet connection.** The add-on fetches EVE's files as you ask for
  them, so there is nothing to download in advance.

That is the whole list. No Node.js, no `granny2.dll`, no separate converter,
and no copy of the EVE client.

## Install

1. Download `carbon_eve_resources-<version>.zip` from the
   [releases page](https://github.com/carbonenginejs/tools-blender/releases).
   **Do not unzip it** — Blender wants the zip itself.
2. In Blender, open **Edit → Preferences → Add-ons**.
3. Click **Install from Disk** and choose the zip.
4. Tick the checkbox beside **CarbonEngineJS** to enable it.

> Do not install `tools-blender-main.zip` from the green *Code* button. That is
> the repository source, not an add-on package, and Blender cannot use it.

## Where it appears

Move your mouse into the **3D viewport** and press **N** to open the sidebar,
then pick the **CarbonEngineJS** tab. You should see three panels:

| Panel | What it is for |
|---|---|
| CarbonEngineJS | Licence acceptance, the download cache, and display settings |
| SOF DNA Builder | Building a ship hull from a DNA string |
| Attribute Editor | Inspecting and editing what you have built |

## Do I need to configure anything?

**No.** The add-on already knows where to find our hosted service, and every
setting in its preferences has a working default. Install it, enable it, and
start loading ships.

## If something goes wrong

**No CarbonEngineJS tab in the sidebar.** The add-on is not enabled, or your
mouse is not in the 3D viewport. Check the tickbox in *Preferences → Add-ons*,
then hover over the viewport and press **N**.

**Blender says it cannot load the add-on.** You are on a version older than
4.0, or you installed the repository zip rather than the release zip.

**Errors mentioning HTTP or a connection.** The add-on could not reach the
service. Check your internet connection and try again in a minute; nothing is
broken on your side, and no setting needs changing.

**A resource fails to download.** Not every path in EVE's `res:/` tree exists,
and paths change between game builds. Check the spelling — a path remembered
from an older build may simply be gone.

## Advanced: pointing it somewhere else

The preferences carry a service address and a **Use local files** option. Both
exist for people developing the tool itself against their own machine. If you
are not doing that, leave them alone — changing them is the most common way to
end up with an add-on that cannot find anything.

## Without Blender

The format readers are published separately and work in plain Python:

```text
pip install carbon-cmf carbon-gr2 carbon-gsf
```

These read geometry only. Browsing, ship assembly and materials are the
add-on's job.
