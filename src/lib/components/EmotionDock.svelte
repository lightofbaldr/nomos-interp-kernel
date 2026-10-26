<script lang="ts">
  import {onMount} from 'svelte';
  import {Toggle, Tooltip} from 'bits-ui';
  import {Dock, DockIcon} from '@nomos/svelte-magic/dock';
  import {Smiley, Leaf, SmileySad, SmileyNervous, SmileyAngry, SmileyMelting, HandHeart, Check} from 'phosphor-svelte';

  const emotions = [
    {id: 'joy', label: 'Joy', icon: Smiley},
    {id: 'calm', label: 'Calm', icon: Leaf},
    {id: 'sadness', label: 'Sadness', icon: SmileySad},
    {id: 'fear', label: 'Fear', icon: SmileyNervous},
    {id: 'anger', label: 'Anger', icon: SmileyAngry},
    {id: 'desperation', label: 'Desperation', icon: SmileyMelting},
    {id: 'compassion', label: 'Compassion', icon: HandHeart}
  ] as const;
  type EmotionId = typeof emotions[number]['id'];

  // UI intent only. No transport, vector, strength or application acknowledgment
  // exists yet. Never feed this state into demo answers, readouts or trace exports.
  // Stay selected across chat changes, but do not persist or auto-arm on reload.
  let selected = $state<EmotionId[]>([]);
  let quietDock = $state(true);
  const selectedLabels = $derived(emotions.filter(e => selected.includes(e.id)).map(e => e.label));
  function toggle(id: EmotionId, pressed: boolean) {
    selected = pressed ? [...selected.filter(value => value !== id), id] : selected.filter(value => value !== id);
  }
  onMount(() => {
    const query = matchMedia('(prefers-reduced-motion: reduce), (hover: none), (pointer: coarse), (max-width: 59.99rem)');
    const update = () => {quietDock = query.matches;};
    update();
    query.addEventListener('change', update);
    return () => query.removeEventListener('change', update);
  });
</script>

<section class="emotion-steering" aria-label="Emotion steering preview" aria-describedby="steering-boundary" data-applied="false" data-motion={quietDock ? 'static' : 'magnify'}>
  <div class="steering-heading">
    <h2>Emotion steering</h2>
    <span class="steering-preview">Preview</span>
    <button class="steering-clear" onclick={() => selected = []} disabled={!selected.length} aria-label="Clear all emotion selections">Clear all</button>
  </div>
  <Tooltip.Provider delayDuration={350} disableHoverableContent>
    {#key quietDock}
      <Dock class="emotion-dock" iconSize={60} iconMagnification={quietDock ? 60 : 72} iconDistance={100} disableMagnification={quietDock} direction="middle">
        {#each emotions as emotion (emotion.id)}
          <DockIcon class="emotion-dock-icon">
            <Tooltip.Root>
              <Tooltip.Trigger>
                {#snippet child({props})}
                  <Toggle.Root {...props} class="emotion-toggle" pressed={selected.includes(emotion.id)} onPressedChange={(pressed) => toggle(emotion.id, pressed)} aria-label={`${emotion.label} steering preview`} aria-describedby={[props['aria-describedby'], 'steering-boundary'].filter(Boolean).join(' ')}>
                    <emotion.icon aria-hidden="true" class="emotion-glyph" size={22} weight={selected.includes(emotion.id) ? 'duotone' : 'regular'}/>
                    <span class="emotion-label">{emotion.label}</span>
                    {#if selected.includes(emotion.id)}<span class="emotion-selected" aria-hidden="true"><Check size={9} weight="bold" aria-hidden="true"/></span>{/if}
                  </Toggle.Root>
                {/snippet}
              </Tooltip.Trigger>
              <Tooltip.Portal>
                <Tooltip.Content role="tooltip" class="emotion-tooltip" side="top" sideOffset={12} collisionPadding={12}>
                  <strong>{emotion.label}</strong>
                  <span>{selected.includes(emotion.id) ? 'Selected · click to turn off' : 'Click to select a steering target'}</span>
                  <small>Preview only. No activations changed.</small>
                </Tooltip.Content>
              </Tooltip.Portal>
            </Tooltip.Root>
          </DockIcon>
        {/each}
      </Dock>
    {/key}
  </Tooltip.Provider>
  <p class="steering-boundary" id="steering-boundary">Not applied · kernel steering is not connected.</p>
  <p class="sr-only" role="status">{selected.length ? `${selectedLabels.join(', ')} selected for preview. No activations changed.` : 'All emotion targets off. No activations changed.'}</p>
</section>

<style>
  .emotion-steering{border-top:1px solid var(--color-rule);margin-top:14px;padding-top:7px}
  .steering-heading{display:flex;align-items:center;gap:8px;min-height:30px}
  .steering-heading h2{font-size:var(--text-xs);font-weight:500;color:var(--color-ink-2);letter-spacing:-.01em}
  .steering-preview{border:1px solid var(--color-rule);border-radius:var(--radius-pill);padding:2px 6px;color:var(--color-muted);font-family:var(--font-mono);font-size:9px;line-height:1.4}
  .steering-clear{margin-left:auto;padding:4px 0 4px 8px;border:0;background:transparent;color:var(--color-muted);font-size:var(--text-xs);min-height:30px}
  .steering-clear:hover:enabled{color:var(--color-ink)}
  .steering-clear:disabled{opacity:.55;cursor:default}
  .steering-boundary{font-size:10px;line-height:1.6;text-align:center;color:var(--color-muted);margin-top:2px}
  :global(.emotion-steering .emotion-dock){margin:0 auto;height:84px;gap:8px;padding:6px 8px;border:0;background:transparent;backdrop-filter:none;align-items:center;border-radius:0;max-width:100%}
  :global(.emotion-steering .emotion-dock-icon){padding:0!important;border-radius:12px;background:transparent;flex-shrink:0}
  :global(.emotion-steering .emotion-dock-icon>div){width:100%;height:100%}
  :global(.emotion-steering .emotion-toggle){position:relative;display:flex;flex-direction:column;align-items:center;justify-content:center;gap:2px;min-width:44px;min-height:44px;width:100%;height:100%;border:1px solid var(--color-rule);border-radius:12px;color:var(--color-muted);background:var(--color-paper-2);transition:background-color 140ms ease,color 140ms ease,border-color 140ms ease}
  :global(.emotion-steering .emotion-toggle:hover){background:var(--color-paper-3);color:var(--color-ink)}
  :global(.emotion-steering .emotion-toggle[aria-pressed=true]){color:var(--color-accent);border-color:var(--color-accent);background:var(--color-accent-soft)}
  :global(.emotion-steering .emotion-label){font-size:10px;line-height:1.1;color:var(--color-ink-2)}
  :global(.emotion-steering .emotion-toggle[aria-pressed=true] .emotion-label){color:var(--color-ink)}
  :global(.emotion-steering .emotion-glyph){flex-shrink:0}
  :global(.emotion-steering .emotion-selected){position:absolute;top:-4px;right:-4px;display:flex;align-items:center;justify-content:center;width:14px;height:14px;border-radius:50%;background:var(--color-accent);color:var(--color-accent-ink);border:2px solid var(--color-paper)}
  :global(.emotion-tooltip){display:grid;gap:5px;max-width:calc(100vw - 24px);padding:12px 14px;z-index:var(--z-modal);border:1px solid var(--color-rule);border-radius:var(--radius-md);background:var(--color-paper-2);color:var(--color-ink);font-size:var(--text-xs);line-height:1.5;box-shadow:0 8px 24px #0004;pointer-events:none}
  :global(.emotion-tooltip strong){font-weight:500}
  :global(.emotion-tooltip[data-state=closed]){visibility:hidden}
  :global(.emotion-tooltip span),:global(.emotion-tooltip small){font-size:var(--text-xs);color:var(--color-muted)}
  @media(max-width:59.99rem){.emotion-steering{margin-top:10px;padding-top:2px}.steering-heading,.steering-clear{min-height:44px}}
  @media(max-width:36rem){:global(.emotion-steering .emotion-dock){width:280px;height:auto;min-height:140px;flex-wrap:wrap}}
  @media(prefers-reduced-motion:reduce){:global(.emotion-steering .emotion-toggle){transition:none}}
</style>
