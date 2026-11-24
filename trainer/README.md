# DeNotive Vectorizer (experimental)

Trains a model that looks at a **picture of a figure** (a screenshot, a JPEG from a paper, a phone photo of a
slide) and **redraws it as editable DeNotive pieces**: shapes, lines and arrows, plot lines and polygons, and
text boxes, which you can paste straight into the evidence editor. Shapes and filled polygons can come back
with **gradient fills**, which you can then adjust with DeNotive's gradient tool (G): drag the circle to turn
the gradient, drag the two lines to move where it starts and ends.

Raster parts of a figure (the image of a heatmap / `imshow` plot, its colour bar, a photo) can't be redrawn as
shapes. The model marks where they are, and `predict` cuts those regions out of your picture at full
resolution. Then it tidies each cut-out:
- **Erases what's drawn on top.** Labels, arrows, boxes and lines that the model found on top of the region
  are rubbed out of the cut-out and filled in from their surroundings (OpenCV inpainting). They already exist
  as their own editable pieces, so they're not doubled up underneath.
- **Traces solid colours into shapes.** If the region is made of a few flat colours (a stacked area chart,
  bars, filled areas), it's traced into one filled shape per colour, like Inkscape's *Trace Bitmap*. The
  shapes come in grouped (Ctrl+Shift+G takes them apart). Specks and leftover bits of erased labels are
  absorbed into the colour around them. Regions with shading or texture (a real heatmap, a photo) stay
  pictures.

So a heatmap comes apart into the picture, the colour bar, the axes and ticks, and every label or arrow drawn
on top, each its own piece. A stacked chart comes apart into its bands. `--no-clean` and `--no-trace` turn
either step off.

It learns entirely from figures it makes up itself. DeNotive draws thousands of random figures: line and
scatter charts (some with shaded areas under a line), bar charts, **stacked area charts** (each coloured band
its own piece, with names like Li₂W₂O₇ written inside the bands or pointed at with arrows, or a legend),
**heatmaps** like an in situ diffraction scan shown with `imshow` (drifting, appearing and vanishing peaks,
smooth maps and noisy textures in viridis, plasma, jet and other colour maps, with colour bars and labels,
arrows, boxes or contour lines on top), flow diagrams and doodles. About a quarter have gradient fills.
The figures come in many shapes: mostly 16:9, but also 4:3, 3:2, square, portrait and wide strips. Some have
extra white space around them or sit off-centre, the way real pictures look once they're fitted onto a slide.
For every picture the right answer (exactly which pieces, where, in what colour) is known. During training the
pictures get steadily worse, as if they were a screenshot of a small, compressed copy. The model learns clean
figures first and damaged ones later.

## Quick start

From this folder (`DeNotive\trainer`), in PowerShell:

```powershell
.\vectorizer.bat generate
```
Makes 60,000 training figures in `data\` (about 5 to 10 minutes and 1 to 2 GB). Run it once.

```powershell
.\vectorizer.bat preview
```
Optional: shows in `runs\damage_preview\` what the blur and JPEG damage look like, from none to the worst.

```powershell
.\vectorizer.bat train
```
Trains. It prints progress every 50 steps and saves every 500. **Ctrl+C pauses** (it saves first); run the same
command again to carry on where it stopped. Every 2,000 steps it writes a picture to `runs\default\samples\`
with three columns: what the model saw, what it drew (rebuilt by DeNotive), and the right answer. Watching those
is the best way to see whether it's learning. `runs\default\log.csv` has the numbers.

```powershell
.\vectorizer.bat predict "C:\path\to\figure.png" --clipboard
```
Redraws a picture with the best model so far. It writes `figure_rebuilt.png` (your picture next to the
rebuild) and `figure_pieces.json` next to the picture, plus `figure_picture1.png`, … for each picture region it
cut out. With `--clipboard`, open an evidence slide in DeNotive and press **Ctrl+V**: the pieces arrive as
normal, editable DeNotive items. The cut-out pictures are added to your DeNotive library first, so they paste
like any other picture. Several pictures at once work too (only the last goes on the clipboard).
`--temperature 0.7` makes it try a different guess each run.

**The words come from Windows' own OCR.** The model is good at *where* text is (box, size, colour, rotation)
but can't make out letters at the size it sees pictures. So `predict` has Windows' built-in OCR read your
full-resolution picture (nothing to install; it's the engine behind the Snipping Tool's text actions):
- the whole picture, enlarged, plus overlapping tiles (OCR reads small areas better);
- the picture turned 90° each way, for vertical axis and colour-bar labels.

Each word goes to the model's text box (or shape) it sits in, and the label moves to where OCR found it.
Boxes with no words are dropped (they were guesses), except tick numbers OCR can't make out, where the model's
guess is kept. Words no box claimed come in as new labels. Then a few common slips are put right:
- chemical formulas come back with subscripts (W03 → WO₃, Nb205 → Nb₂O₅, Li2VV207 → Li₂W₂O₇);
- "oc" becomes °C;
- diffraction labels: 29/20 before (deg) or ° becomes 2θ, and "X = 0.2099 Å" becomes "λ = 0.2099 Å".

On held-back practice figures this took word labels from 1% to 61% exactly right, and number labels from 32%
to 72%. Heavily blurred pictures read worse (11% and 35%). Other superscripts come back as plain characters
("cm-1"), so retype those in DeNotive if needed. `--no-ocr` shows the model's own guesses instead.
A tick number OCR couldn't make out keeps the model's guess only if it fits the axis: it lines up with at least
two numbers OCR did read and has the value that belongs at its place (so stray "4.0"s inside a chart go).
OCR misses some symbols on its own (a "°C" after "Temperature,", say): add those by hand.

**Exact shapes come from your picture too.** The model decides *what* each piece is (a plot line, a filled
band, a frame, its colour and dash style) but writes at most 48 points per line on a coarse grid. So `predict`
takes the shapes from your picture's own pixels:
- **Plot lines** are followed pixel column by pixel column in their own colour, starting from the model's rough
  line so they stay on the right series. Sharp peaks keep their full height, and an XRD pattern comes back with
  hundreds of points, to about a pixel. The colour, line thickness and dash style are measured from the picture.
- **Lines the model missed** are found straight from the picture: long, thin runs of one colour (frames, axes,
  grid lines and filled bands are told apart by their shape). Dense patterns with hundreds of points and sharp
  peaks look nothing like the model's practice figures, so this is how those come through. A short level stroke
  in a plot line's colour becomes its legend sample.
- **Filled areas** (stacked bands, shaded regions) are traced in the picture's exact colours, right up to the
  frame, with the labels on top left out of them.
- **Frames and boxes** snap onto the straight outline in the picture. Boxes the picture doesn't show (the
  model's guesses) are dropped, a box a shade off takes the picture's colour, and coloured lines with nothing
  under them go.

Anything the picture doesn't back up keeps the model's version. `--no-refine` turns all of this off.

## Predict Shapes in DeNotive

In DeNotive's evidence editor, right-click a picture → **Predict Shapes**. DeNotive hands the picture to this
folder (`main.py shapes`, run with a Python that has PyTorch, OpenCV, SciPy and PySide6) and gets the pieces back
as one group exactly over the picture, which it hides underneath. What reads the picture is set in DeNotive's
**Settings → AI settings… → Image to shape prediction**:
- **Claude** (Opus by default), or whichever AI the Co-Investigator or note drafting uses, with a choice of its models. It writes
  the pieces itself from the same kind of description the model learns; OCR then measures how big each label
  really is and which way a vertical label reads (AIs tend to make labels too small and mix up the direction).
- **the built-in model** (this folder's `runs\default\best.pt`), with Windows OCR for the words.

If the AI can't answer (not signed in, usage limit reached, no usable reply), the built-in model steps in, and
DeNotive says why. The exact tracing (see above) always runs. A test on 8 figures (`runs\claude_test`) found
Claude Opus reads labels best, every formula with its subscripts, and its shapes are good. With tracing they're
exact. Haiku is quicker on the plan's limits but rougher.

DeNotive looks for this folder beside its own code (or above `DeNotive.exe`) and for a Python that has the
packages. The settings section says what it found.

## How long it takes on this PC

There's no NVIDIA graphics card, so it trains on the processor. The default "small" model (6.4M parameters,
512 × 288 pictures) takes about 1 to 1.5 seconds per step of 12 figures. The default 30,000 steps is roughly
10 hours, so an overnight run. The damage reaches its worst at 70% of the way (`--ramp 0.7`). For a quicker
first look, try `--steps 8000` (about 2.5 hours): you'll see it start placing boxes, lines and colours.

What to expect, honestly:
- It learns the **layout first**: where boxes, axes, ticks and plot lines are, their colours, line widths and
  dashes. These come out recognisably within a few thousand steps on clean pictures.
- **Reading text** is beyond the small model. At 512 pixels wide a tick label is only 5–10 pixels tall, a
  fraction of one of the 16 × 16 patches the model looks through, so it learns to guess common words instead
  (a 30,000-step run got about 1% of word labels right on its own). It does place the text boxes well, so
  `predict` lets Windows OCR read them (see above). A graphics card with the "base" preset (768 × 432
  pictures, a bigger model) gives the model a better chance of reading by itself.
- **Real figures differ from the made-up ones** (other fonts, radial gradients, photos, 3-D plots, logos). Expect
  rough results on those until the generator makes figures that look more like yours (see *Making it better*).

## Using a graphics card

The trainer uses a graphics card automatically when PyTorch can see one, and prints which device it's on.
- **NVIDIA** (another PC, or a cloud machine): install the CUDA build of PyTorch from pytorch.org, then
  `.\vectorizer.bat train --preset base --batch 32 --steps 80000`.
- **Intel graphics in this laptop**: PyTorch has an "XPU" build for Intel GPUs, but it officially targets
  Arc graphics; this laptop's Core Ultra 7 155U graphics may or may not work. To try it, in a separate Python
  environment, run `pip install torch --index-url https://download.pytorch.org/whl/xpu`. Then
  `python -c "import torch; print(torch.xpu.is_available())"` should print True.

A model trained with one preset keeps it. To start a second model, give it its own folder:
`--run runs\big --preset base`. `predict --ckpt runs\big\best.pt` uses it.

## Options worth knowing

| Command | Option | What it does |
|---|---|---|
| generate | `--count 100000` | More figures (more variety, less chance of memorising) |
| train | `--steps`, `--batch`, `--lr` | Length of training, figures per step, learning rate |
| train | `--ramp 0.7`, `--max-level 1.0` | When the damage peaks, and how bad it gets (1.0 is quite blurry) |
| train | `--fresh` | Start over instead of carrying on |
| predict | `--ckpt runs\default\last.pt` | Use the latest save instead of the best one |
| predict | `--no-clean` | Leave labels drawn on pictures inside the cut-outs |
| predict | `--no-trace` | Keep solid-colour regions as pictures instead of tracing them into shapes |
| predict | `--no-ocr` | Use the model's own guesses at the words instead of Windows OCR |
| predict | `--no-refine` | Keep the model's own (rougher) lines, areas and boxes instead of taking them from the picture |

## How it works

- `vectorizer/generate.py` makes random figures, drawn by DeNotive's own slide code so the picture and its
  answer always agree. Each figure is laid out on a canvas of a random shape (see `FRAMES` for the shapes
  and how often each is used), then shrunk and centred onto the 16:9 slide with white margins, exactly as
  `predict` fits a real picture. 35% get extra margin and a quarter sit off-centre. Text sizes and line widths
  are snapped to ones the model can say, so the answers stay exact. Add your own figure styles here.
- `vectorizer/degrade.py` adds the damage, in the order it happens in real life: a smaller original, saved
  as a JPEG, shown enlarged on screen, a little blur, screenshotted at screen size, then saved as a JPEG again.
- `vectorizer/tokens.py` writes a figure as a short sentence of tokens (`<shape> rect x y x y fill border width
  …`, up to 960 tokens), with positions on a 320-step grid and colours from 216 shades. Text can use
  superscript and subscript digits (cm⁻¹, Li₂W₂O₇). A picture region is just `<image> x y x y`. A filled piece may add a gradient:
  `<grad>`, the second colour, the direction (15° steps) and where the two edge lines sit. That's DeNotive's own
  gradient with its circle centred. Moving the centre along the gradient is the same as moving both lines, so
  nothing is lost. A grammar makes sure the model can only ever write sentences that turn back into valid
  DeNotive pieces.
- `vectorizer/ocr.py` reads the words with Windows OCR and `vectorizer/refine.py` takes the exact lines,
  areas and frames from the picture (see above); `vectorizer/cleanup.py` erases labels from picture regions
  and traces flat colours.
- `vectorizer/model.py` is the model. A small convolutional network reads the picture; a transformer writes the
  sentence one token at a time while looking at the picture. This is the same design used in research on
  turning charts and diagrams back into data or vector graphics (Pix2Seq, Donut, StarVector), scaled down for a
  laptop.

## Making it better

- **Make the made-up figures look like yours.** This matters most. If your figures are XRD patterns, add a
  generator with many sharp peaks, log axes and inset labels. If they're microscope panels with scale bars,
  add those.
- **More steps and more figures** keep helping for a long time.
- **Real examples**: rebuild a few of your own figures by hand in DeNotive (or import their PDFs, which gives
  exact pieces). Pairs of real pictures with their DeNotive pieces could later fine-tune the model on the real
  thing.

The training data (`data\`) and models (`runs\`) stay on this PC and are not part of the DeNotive app or its
GitHub repository.
