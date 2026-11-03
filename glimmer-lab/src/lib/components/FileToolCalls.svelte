<script lang="ts">
  import * as Tool from '@nomos/svelte-ai-elements/tool';
  import type {Message} from '@earendil-works/pi-ai';
  import type {EntryRecord} from 'glimmer-agent/pi';
  import {textOf} from '$lib/pi/model';
  let {message,entries,streaming}:{message:Message;entries:EntryRecord[];streaming:boolean}=$props();
  const calls=$derived(message.role==='assistant'?message.content.filter(b=>b.type==='toolCall'):[]);
  const results=$derived(entries.flatMap(e=>e.model??[]).filter(m=>m.role==='toolResult'));
  type Phase={exit_code:number|null;reason:string|null;stdout:string;stderr:string};
  function mojoOutput(text:string):{compile:Phase;run:Phase|null}|null{
    try{
      const value=JSON.parse(text);
      const valid=(p:Phase)=>p&&typeof p==='object'&&(p.exit_code===null||Number.isInteger(p.exit_code))&&(p.reason===null||typeof p.reason==='string')&&typeof p.stdout==='string'&&typeof p.stderr==='string';
      return valid(value.compile)&&(value.run===null||valid(value.run))?value:null;
    }catch{return null;}
  }
  function shellOutput(text:string):Phase|null{
    try{const v=JSON.parse(text);return v&&typeof v==='object'&&(v.exit_code===null||Number.isInteger(v.exit_code))&&(v.reason===null||typeof v.reason==='string')&&typeof v.stdout==='string'&&typeof v.stderr==='string'?v:null;}catch{return null;}
  }
</script>
{#each calls as call (call.id)}
  {@const result=results.find(r=>r.toolCallId===call.id)}
  <div class="file-tool" data-testid="file-tool-call">
    <Tool.Root><Tool.Header type={call.name} state={result?result.isError?'output-error':'output-available':streaming?'input-streaming':'input-available'}/><Tool.Content>
      <p>{call.name==='bash'?'Recorded sandboxed shell command. No network or host home; commands can mutate the conversation workspace. Output is untrusted, not proof of correctness.':call.name==='mojo'?'Recorded sandboxed Mojo build/run. Program output is untrusted, not a correctness grade; source workspace stays read-only.':'Recorded Pi tool call. File tools are confined to this conversation’s sandboxed /work.'}</p>
      <pre aria-label={`${call.name} arguments`}>{JSON.stringify(call.arguments,null,2)}</pre>
      {#if result}
        {@const mojo=call.name==='mojo'?mojoOutput(textOf(result)):null}
        {@const shell=call.name==='bash'?shellOutput(textOf(result)):null}
        {#if shell}<section aria-label="Sandboxed shell output"><h4>Shell · exit {shell.exit_code??'unknown'}{shell.reason?` · ${shell.reason}`:''}</h4>{#if shell.stdout}<pre aria-label="Shell stdout">{shell.stdout}</pre>{/if}{#if shell.stderr}<pre aria-label="Shell stderr">{shell.stderr}</pre>{/if}</section>{/if}
        {#if mojo}
          {#each [{name:'Compile',value:mojo.compile},{name:'Run',value:mojo.run}] as phase}
            <section aria-label={`Mojo ${phase.name.toLowerCase()} output`}>
              <h4>{phase.value?`${phase.name} · exit ${phase.value.exit_code??'unknown'}${phase.value.reason?` · ${phase.value.reason}`:''}`:`${phase.name} · not started`}</h4>
              {#if phase.value?.stdout}<pre aria-label={`Mojo ${phase.name.toLowerCase()} stdout`}>{phase.value.stdout}</pre>{/if}
              {#if phase.value?.stderr}<pre aria-label={`Mojo ${phase.name.toLowerCase()} stderr`}>{phase.value.stderr}</pre>{/if}
              {#if phase.value&&!phase.value.stdout&&!phase.value.stderr}<p>No stdout or stderr recorded.</p>{/if}
            </section>
          {/each}
          <p>Full recorded result, including sandbox evidence</p>
        {/if}
        <pre aria-label={`${call.name} result`}>{textOf(result)}</pre>
      {:else}<p>Awaiting a durable result. This is not a completion claim.</p>{/if}
    </Tool.Content></Tool.Root>
  </div>
{/each}
<style>
  .file-tool{margin:12px 0;color:var(--color-ink);overflow:hidden}.file-tool p{font-size:11px;color:var(--color-muted);margin:8px 12px}.file-tool h4{font-size:12px;font-weight:500;color:var(--color-ink);margin:12px}.file-tool pre{color:var(--color-ink);white-space:pre-wrap;overflow-wrap:anywhere;max-height:260px;overflow:auto;font:11px/1.6 var(--font-mono);padding:12px;background:var(--color-paper-2)}
</style>
