# Muse mascot background

Source: Adam's supplied `/home/adam/Downloads/muse.png`, preserved as `muse-source.png`. Edited with the built-in image-generation/editing tool, not the API/CLI fallback. This is a generated background extraction, not a claim of pixel-exact source preservation or ownership of Meta's brand asset.

Selected edited output: `muse-mascot-cutout.png`. Browser version: `../../static/img/muse-mascot.webp`, format-encoded with alpha retained. The original Downloads file was not overwritten. The mascot is decorative and stays independent of every model/probe state.

## Prompt

Use case: background-extraction.
Asset type: a transparent mascot cutout for an existing dark chat application's subtle background.
Input image 1 is the EDIT TARGET, not a style reference: /home/adam/Downloads/muse.png.
Primary request: isolate only the existing cream-colored plush Muse mascot on the RIGHT, including the glowing blue-purple orb held in its hands. Remove the pale/white background completely, and remove the entire left-hand M icon and the word 'Muse'.
Invariants: preserve the exact supplied mascot's face, small black eyes, smile, pink cheeks, proportions, cream fur texture, pose, hands, and blue-purple orb. Do not redesign, re-pose, reinterpret, or add accessories. Preserve its original lower-body crop; do not invent missing feet/body. Preserve soft fine fur edges and the orb glow naturally.
Output: one centered, tight-but-unclipped character cutout on ACTUAL fully transparent alpha, clean antialiased edges. No text, logos, floor, scene, shadow rectangle, white fringe, colored background, or fake checkerboard pattern.

## Placement

Static low-opacity watermark inside `.conversation-stage` only; no motion, click handling or probe-driven styling. Pointer events disabled and empty alt/aria-hidden exclude decoration from the accessibility tree. The composer, header, sidebar and inspector retain their original opaque backgrounds.
