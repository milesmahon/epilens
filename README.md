# epilens
Visualize stereo-EEG electrodes on a 3D model brain.

https://github.com/user-attachments/assets/8144d726-86a7-4a2a-8bd7-b70d957ca9ab

## How to run
```
$ git clone <this repo>
$ cd <this repo>
$ uv sync
$ uv run epilens
```

Place electrodes by typing them, e.g.:

```
> STG RHH # entry, target
> RPUL # defaults to lateral entry
> orbitofrontal, lhh # infers LOF
```

Supports tabs/multiple plans and allows manual movement of the electrodes

Coordinates are in generic MNI space

### More electrode entry examples:
| You type | You get |
|---|---|
| `STG RHH` | entry R superior temporal gyrus, target R hippocampal head |
| `OFC LHB` | entry L orbitofrontal cortex, target L hippocampal body |
| `RPUL` | target R pulvinar, straight lateral entry (the default when no entry is given) |
| `from left MTG to the left head of hippocampus, 10 contacts` | same idea, written out |
| `target -25 -20 -15 entry lateral` | exact MNI coordinates |

### CSV/Excel
Supports import and export to/from csv/excel: one row per contact, with contact labels like `LAH1` and MNI coordinates (columns
such as `MNI X`, or `MNI X,Y,Z_1` to `_3`). Grids and strips are skipped.

**Adding words:** abbreviations and phrasings are in `src/epilens/data/aliases.csv`
(`alias,base,modifier`).

## 

```bash
uv sync
uv run pytest                           # browser tests use the system google-chrome
uv run epilens                          # http://localhost:8737/
uv run python scripts/build_atlas.py    # rebuild src/epilens/data (downloads atlases)
```
