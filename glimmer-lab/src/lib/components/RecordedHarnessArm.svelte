<script lang="ts">
  import * as Tool from '@nomos/svelte-ai-elements/tool';
  import type {RecordedArm} from '$lib/workbench/recorded';
  let {arm}:{arm:RecordedArm}=$props();
  let index=$state(0);
  const event=$derived(arm.events[index]);
  const compilerIndex=$derived(arm.events.findIndex(e=>e.name==='bash'&&String(e.args.command??'').includes('check_mojo.py')));
  function move(next:number){index=Math.max(0,Math.min(arm.events.length-1,next));}
  const description=(args:Record<string,unknown>)=>String(args.command??args.path??args.pattern??'Saved tool call');
</script>

<article class="recorded-arm" aria-label={`${arm.label} recorded rollout`}>
  <header class="arm-heading"><div><span class="arm-kicker">FROZEN HARNESS</span><h2>{arm.label}</h2></div><span class:passed={arm.grade.passed} class="grader-verdict">Grader {arm.grade.passed?'PASS':'FAIL'}</span></header>
  <div class="arm-identity"><code title={arm.harnessSha256}>{arm.harnessSha256.slice(0,12)}</code><span>{arm.modelCalls} model calls · {arm.events.length} tool calls</span></div>
  <p class="arm-result">{#if arm.grade.compile.returncode!==0}Submitted program did not compile.{:else if arm.grade.passed}Submitted program passed the external test.{:else}Submitted program failed the external test.{/if}</p>
  <div class="event-nav"><button onclick={()=>move(index-1)} disabled={index===0} aria-label={`${arm.label} previous event`}>←</button><label><span>Saved tool event</span><select aria-label={`${arm.label} saved event`} bind:value={index}>{#each arm.events as e,i}<option value={i}>{i+1} / {arm.events.length} · {e.name}</option>{/each}</select></label><button onclick={()=>move(index+1)} disabled={index===arm.events.length-1} aria-label={`${arm.label} next event`}>→</button></div>
  <p class="event-navigation-note">Each side steps independently. Event numbers are not time-aligned.</p>
  <div class="recorded-tool" data-event={index+1}>
    <Tool.Root open={true} class="recorded-tool-root">
      <Tool.Header type={event.name} state={event.isError?'output-error':'output-available'} title={`${event.name} · recorded result`}/>
      <Tool.Content class="recorded-tool-content">
        <p class="event-description">{description(event.args)}</p>
        <p class="event-origin">JSONL lines {event.startLine}–{event.endLine} · {(event.durationMs/1000).toFixed(2)} s recorded</p>
        <pre class="event-output" aria-label={`${arm.label} saved tool output`}>{event.output||'(Empty recorded output)'}</pre>
        <details><summary>Exact arguments</summary><pre aria-label={`${arm.label} saved tool arguments`}>{JSON.stringify(event.args,null,2)}</pre></details>
        <details><summary>Assistant message before this call</summary><h4>Recorded reasoning</h4><pre>{event.reasoning||'(No reasoning text in this message)'}</pre><h4>Recorded text</h4><pre>{event.assistant||'(No assistant text in this message)'}</pre></details>
      </Tool.Content>
    </Tool.Root>
  </div>
  <div class="trace-jumps"><button onclick={()=>move(arm.events.findIndex(e=>e.name==='write'))}>First source write</button>{#if compilerIndex>=0}<button onclick={()=>move(compilerIndex)}>First compiler feedback</button><button onclick={()=>move(arm.events.findLastIndex(e=>e.name==='bash'&&String(e.args.command??'').includes('check_mojo.py')))}>Last self-check</button>{:else}<span>No compiler call in this trace.</span>{/if}</div>
  <details class="arm-section"><summary>Final submitted source <span>Not the source at the selected step</span></summary><pre class="source">{arm.source}</pre><p class="hash">SHA-256 {arm.sourceSha256}</p></details>
  <details class="arm-section grader-details"><summary>External grader <span>After the rollout · separate from tool events</span></summary>
    <p>Oracle: {arm.grade.oracle}. Comparison: {arm.grade.normalization}.</p>
    <h4>Compile · exit {arm.grade.compile.returncode}</h4><pre>{arm.grade.compile.stderr||arm.grade.compile.stdout||'(No compiler output)'}</pre>
    {#if arm.grade.run}<h4>Run · exit {arm.grade.run.returncode}</h4><pre>{arm.grade.run.stdout||'(Empty stdout)'}</pre>{#if arm.grade.run.stderr}<pre>{arm.grade.run.stderr}</pre>{/if}{:else}<p>Run: not attempted after the compile failure.</p>{/if}
    <p>Recorded reason: {arm.grade.reason??'none supplied'}. A pass covers this task’s recorded tests, not every possible input. Muse’s quality opinion is not used for this verdict.</p>
  </details>
  <details class="arm-section"><summary>Final assistant answer</summary><pre>{arm.finalAnswer}</pre></details>
  <details class="arm-section"><summary>Identity &amp; provenance</summary><dl><dt>Harness SHA-256</dt><dd>{arm.harnessSha256}</dd><dt>Frozen tree</dt><dd>{arm.directory}</dd><dt>Session</dt><dd>{arm.sessionId}</dd><dt>Started</dt><dd>{arm.timestamp}</dd><dt>Recorded wall time</dt><dd>{arm.seconds.toFixed(2)} s</dd><dt>Launch SHA-256</dt><dd>{arm.launchSha256}</dd><dt>Trace SHA-256</dt><dd>{arm.traceSha256}</dd><dt>Completion SHA-256</dt><dd>{arm.completionSha256}</dd><dt>Grade SHA-256</dt><dd>{arm.gradeSha256}</dd></dl></details>
</article>

<style>
  .recorded-arm{min-width:0;border:1px solid var(--color-rule);border-radius:12px;padding:18px;background:var(--color-inspector)}
  .arm-heading{display:flex;align-items:center;justify-content:space-between;gap:10px}.arm-heading h2{font-size:25px;letter-spacing:-.8px;margin:3px 0 0}.arm-kicker{font-size:10px;letter-spacing:1.4px;color:var(--color-muted)}
  .grader-verdict{font-size:11px;border:1px solid #aa675b;color:#f4b4a7;padding:6px 9px;border-radius:5px;white-space:nowrap}.grader-verdict.passed{color:#b5dfca;border-color:#467e67}
  .arm-identity{display:flex;flex-wrap:wrap;gap:6px 12px;margin:10px 0;color:var(--color-muted);font-size:11px}.arm-identity code{color:var(--color-ink)}.arm-result{font-size:13px;min-height:36px;margin:12px 0 18px;line-height:1.5}
  .event-nav{display:flex;gap:8px;align-items:end}.event-nav label{display:flex;flex:1;min-width:0;flex-direction:column;gap:6px;font-size:11px;color:var(--color-muted)}.event-nav select{width:100%;min-width:0}.event-nav button,.event-nav select,.trace-jumps button{border:1px solid var(--color-rule);border-radius:5px;background:var(--color-paper-2);color:var(--color-ink);padding:8px;font:inherit;font-size:12px}.event-nav button{min-width:34px;cursor:pointer}.event-nav button:disabled{opacity:.35;cursor:default}
  .event-navigation-note{font-size:10px;line-height:1.5;color:var(--color-muted);margin:8px 0 12px}.recorded-tool{min-width:0;font-size:12px}.recorded-tool :global(.recorded-tool-root){margin:0;border-color:var(--color-rule)}
  /* AI Elements' popover foreground belongs to a different theme. Scope the
     application ink here rather than altering that package or global tokens. */
  .recorded-tool :global(.recorded-tool-content){padding:10px;color:var(--color-ink);animation:none}.event-description{font:11px/1.5 var(--font-mono,monospace);overflow-wrap:anywhere;margin:0 0 8px}.event-origin{font-size:10px;color:var(--color-muted);margin:8px 0}
  pre{font:11px/1.55 var(--font-mono,monospace);white-space:pre-wrap;overflow-wrap:anywhere;max-height:320px;overflow:auto;padding:10px;border-radius:5px;background:var(--color-paper-2);margin:8px 0;tab-size:4}.event-output{min-height:95px;max-height:200px}.source{white-space:pre;overflow-wrap:normal}
  details{font-size:12px;margin:8px 0}summary{cursor:pointer;line-height:1.5}summary span{display:block;font-size:10px;color:var(--color-muted);margin-left:13px}h4{font-size:11px;font-weight:600;margin:10px 0 5px}.trace-jumps{display:flex;flex-wrap:wrap;gap:6px;margin:12px 0 18px;align-items:center}.trace-jumps button{font-size:10px;cursor:pointer}.trace-jumps span{font-size:11px;color:var(--color-muted)}
  .arm-section{border-top:1px solid var(--color-rule);padding-top:10px;margin:10px 0 0}.arm-section p{font-size:11px;line-height:1.6;color:var(--color-muted)}.hash,dd{overflow-wrap:anywhere;font:10px/1.5 var(--font-mono,monospace)}dl{margin:10px 0}dt{font-size:10px;color:var(--color-muted);margin-top:10px}dd{margin:3px 0}
  button:focus-visible,select:focus-visible,summary:focus-visible,pre:focus-visible{outline:2px solid #b8cfec;outline-offset:3px}
</style>
