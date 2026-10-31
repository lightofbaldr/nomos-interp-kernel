<script lang="ts">
  import {emptyContent,parseSkill,profileSize,validateContent,FILE_TOOLS,type FileTool,type ProfileState,type ProfileContent,type ProfileRevision} from 'glimmer-agent/profile-model';
  import type {ToolAvailability} from '$lib/server/file-broker';
  import type {ImportPreview} from '$lib/server/profile-import';
  import type {LiveState} from '$lib/live';
  import ProfileDetails from './ProfileDetails.svelte';
  let {live}:{live:LiveState}=$props();
  let profile=$state<ProfileState|null>(null),draft=$state<ProfileContent>(emptyContent()),base=$state(''),expected=$state(0);
  let loaded=$state(''),waiting=$state(false),error=$state(''),notice=$state(''),skillText=$state('');
  let roots=$state<{id:number;label:string}[]>([]),root=$state(0),importPath=$state(''),useBase=$state(false),baseRoot=$state(0),basePath=$state('');
  let preview=$state<ImportPreview|null>(null),allowImportLarge=$state(false);
  let tools=$state<ToolAvailability>({available:false,reason:'Isolation not checked.',policy:'glimmer.files/bwrap-1'});
  let requestSerial=0,observed='';
  const dirty=$derived(JSON.stringify(draft)!==base);
  const validation=$derived.by(()=>{try{validateContent(draft);return '';}catch(e){return e instanceof Error?e.message:'Invalid profile.';}});
  const size=$derived.by(()=>{try{return profileSize(draft);}catch{return null;}});
  const history=$derived((live.pi?.entries??[]).filter(e=>e.kind==='glimmer.profile.changed').map(e=>({entry:Number(e.id),data:e.data as {action:string;active:ProfileRevision;pending:ProfileRevision|null}})).filter(e=>e.data.action!=='pending').reverse());
  function hydrate(p:ProfileState){profile=p;draft=structuredClone((p.pending??p.active)!.content);base=JSON.stringify(draft);expected=p.version;preview=null;}
  async function load(id:string,reset=false){
    const serial=++requestSerial;
    try{const response=await fetch(`/api/pi/profile?conversation=${encodeURIComponent(id)}`),data=await response.json();
      if(serial!==requestSerial||id!==live.conversation)return;if(!response.ok)throw Error(data.error);
      const changed=loaded!==id;loaded=id;roots=data.roots;tools=data.tools;
      if(changed||reset||!dirty)hydrate(data.profile);
      else {profile=data.profile;if(expected!==data.profile.version)notice='Saved profile changed elsewhere. Your unsaved draft is kept; reload before saving.';}
      error='';
    }catch(e){if(serial===requestSerial&&id===live.conversation)error=e instanceof Error?e.message:String(e);}
  }
  $effect(()=>{
    const id=live.conversation,connected=live.pi?.connected;
    // Metadata events drive refreshes; ordinary streamed tokens do not replace the form.
    const changed=live.pi?.entries.findLast(e=>e.kind==='glimmer.profile.changed')?.id;
    const key=`${id}:${connected}:${changed}`;if(key===observed)return;observed=key;
    if(id&&connected)void load(id);
  });
  async function action(body:Record<string,unknown>){
    if(waiting||!live.conversation)return;waiting=true;error='';notice='';const id=live.conversation;
    try{const r=await fetch('/api/pi/profile',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({...body,conversation:id})}),data=await r.json();
      if(!r.ok)throw Error(data.error??'Profile operation failed.');if(id!==live.conversation)return;
      if(data.profile){hydrate(data.profile);notice=data.profile.pending?'Saved pending. Applies before the next user submission after this run settles.':'Profile applied.';}
      if(data.preview){preview=data.preview;allowImportLarge=false;}
      if(data.tools)tools=data.tools;
      if(data.conversation)await live.openConversation?.(data.conversation);
    }catch(e){if(id===live.conversation){error=e instanceof Error?e.message:String(e);if(body.action==='checkTools')tools={...tools,available:false,reason:error};}}finally{waiting=false;}
  }
  function addSkill(){try{parseSkill(skillText);draft.skills.push({markdown:skillText,enabled:true});skillText='';error='';}catch(e){error=String(e);}}
  function toggleTool(name:FileTool,enabled:boolean){draft.tools=FILE_TOOLS.filter(t=>t===name?enabled:(draft.tools??[]).includes(t));}
  function skillName(text:string,i:number){try{return parseSkill(text).name;}catch{return `Skill ${i+1} (needs valid frontmatter)`;}}
  async function upload(event:Event){const input=event.currentTarget as HTMLInputElement,file=input.files?.[0];if(!file)return;
    try{if(file.size>16384)throw Error('SKILL.md exceeds 16 KiB.');skillText=new TextDecoder('utf-8',{fatal:true}).decode(await file.arrayBuffer());parseSkill(skillText);}catch(e){error=String(e);}finally{input.value='';}}
</script>
<div class="agent-profile">
  <header><h3>Agent profile</h3><span data-testid="profile-state">{loaded!==live.conversation?'Loading':dirty?'Unsaved':profile?.pending?'Pending':'Active'}</span></header>
  <p>Instructions, inline skills and optional sandboxed file tools for this Pi conversation.</p>
  {#if profile?.active&&loaded===live.conversation}
    <div class="revision"><ProfileDetails profile={profile.active} label={`Active r${profile.active.revision}`}/>{#if profile.pending}<ProfileDetails profile={profile.pending} label={`Pending r${profile.pending.revision}`}/>{/if}</div>
    <p>Edits during a run wait until the next user submission, after the whole model/tool run settles.</p>
    <fieldset disabled={waiting}>
      <label>Instructions<textarea aria-label="Profile instructions" bind:value={draft.instructions} rows="8" spellcheck="false"></textarea></label>
      <div class="budget" data-testid="profile-budget">{#if size}{size.characters.toLocaleString()} rendered characters · ~{size.approximateTokens.toLocaleString()} estimated tokens / 32,768 context{:else}Size unavailable until skill frontmatter is valid.{/if}
        <small>Character/4 estimate, not tokenizer usage or remaining context. Conversation, template and response need space too.</small></div>
      <label class="check"><input type="checkbox" bind:checked={draft.allowLarge}/>Allow more than 6,000 rendered characters (64 KiB hard limit)</label>
      <h4>File tools <span>SANDBOXED · OFF BY DEFAULT</span></h4>
      <p data-testid="tools-availability">{tools.available?'Tools available':'Tools unavailable'} — {tools.reason}</p>
      <button onclick={()=>action({action:'checkTools'})}>Check tool isolation</button>
      <div class="tool-toggles">{#each FILE_TOOLS as name}<label class="check"><input type="checkbox" aria-label={`Enable tool ${name}`} checked={(draft.tools??[]).includes(name)} disabled={!tools.available&&!(draft.tools??[]).includes(name)} onchange={e=>toggleTool(name,e.currentTarget.checked)}/>{name}</label>{/each}</div>
      <p>Only this session's /work: UTF-8 files up to 1 MiB; bounded results; no shell, compiler, network or host home. A fork starts with empty files. Restore changes the profile, not files. Interrupted writes may have completed: inspect before retrying.</p>
      <p>Workspace ceiling: 256 MiB / 8,192 entries (checked per operation, not a filesystem quota). Tool schemas use additional context beyond the estimate above.</p>
      <h4>Skills <span>INLINE</span></h4><p>Enabled skills are sent in full. They are not materialized as workspace files; on-demand skill delivery is not implemented.</p>
      {#each draft.skills as skill,i}
        <section class="skill"><div class="skill-heading"><label class="check"><input type="checkbox" aria-label={`Enable ${skillName(skill.markdown,i)}`} bind:checked={skill.enabled}/>{skillName(skill.markdown,i)}</label><button onclick={()=>draft.skills.splice(i,1)} aria-label={`Remove skill ${i+1}`}>Remove</button></div>
          <textarea aria-label={`Skill ${i+1} Markdown`} bind:value={skill.markdown} rows="6" spellcheck="false"></textarea></section>
      {/each}
      <label>New SKILL.md<textarea aria-label="New skill Markdown" bind:value={skillText} rows="5" placeholder={'---\nname: careful_answers\ndescription: Check assumptions\n---\nState uncertainty when evidence is missing.'} spellcheck="false"></textarea></label>
      <label>Load Markdown file<input type="file" accept=".md,text/markdown,text/plain" onchange={upload}/></label>
      <button onclick={addSkill} disabled={!skillText||draft.skills.length>=16}>Add inline skill</button>
      {#if draft.advisory.length}<h4>Imported advisory sections</h4><p>Prompt text only; not enforced guardrails or tool schemas.</p>{#each draft.advisory as section,i}<section class="skill"><label class="check"><input type="checkbox" bind:checked={section.enabled}/>{section.name}</label><textarea aria-label={`Advisory ${i+1}`} bind:value={section.text} rows="4"></textarea><button onclick={()=>draft.advisory.splice(i,1)}>Remove advisory {i+1}</button></section>{/each}{/if}
      {#if validation}<p class="warning">{validation}</p>{/if}
      <button class="primary" onclick={()=>action({action:'save',version:expected,content:draft})} disabled={Boolean(validation)||!dirty}>Save profile</button>
      <button onclick={()=>{notice='';void load(live.conversation!,true);}}>Reload saved profile</button>
    </fieldset>
    <section class="imports"><h4>Import Harness²</h4>
      <p>Preview a text subset. Extensions appear as escaped code only and never run. Applying replaces the editor with the previewed snapshot.</p>
      {#if !roots.length}<p>Imports unavailable. The operator must configure GLIMMER_HARNESS_IMPORT_ROOTS on the server.</p>{:else}
        <fieldset disabled={waiting||dirty}>
          <label>Import root<select bind:value={root}>{#each roots as r}<option value={r.id}>{r.label}</option>{/each}</select></label>
          <label>Harness relative path<input aria-label="Harness relative path" bind:value={importPath} placeholder="harnesses/improved"/></label>
          <label class="check"><input type="checkbox" bind:checked={useBase}/>Compare with a separate base harness</label>
          {#if useBase}<label>Base root<select bind:value={baseRoot}>{#each roots as r}<option value={r.id}>{r.label}</option>{/each}</select></label><label>Base relative path<input bind:value={basePath}/></label>{/if}
          <button onclick={()=>action({action:'preview',root,path:importPath,...(useBase?{base:{root:baseRoot,path:basePath}}:{})})}>Preview import and diff</button>
        </fieldset>
        {#if dirty}<p>Save or reload your draft before importing.</p>{/if}
      {/if}
      {#if preview}<div class="import-preview" data-testid="import-preview"><h4>Preview only</h4>{#each preview.warnings as warning}<p class="warning">{warning}</p>{/each}
        <p>Reference: {preview.reference}</p><p>Snapshot <code>{preview.digest}</code></p>
        <div class="diff"><details><summary>Before — {preview.reference}</summary><pre>{preview.before||'(empty)'}</pre></details><details open><summary>After — imported text</summary><pre>{preview.after}</pre></details></div>
        <p>{preview.before===preview.after?'Rendered text is unchanged.':'Rendered text differs. Both complete texts are shown; no hidden execution settings are imported.'}</p>
        <ul>{#each preview.files as f}<li><code>{f.path}</code> · {f.disposition}</li>{/each}</ul>
        {#each preview.extensions as code}<details><summary>{code.path} — never executed</summary><pre>{code.text}</pre></details>{/each}
        <label class="check"><input type="checkbox" bind:checked={allowImportLarge}/>Allow a preview exceeding 6,000 characters</label>
        <button class="primary" disabled={waiting} onclick={()=>action({action:'import',preview:preview!.id,version:preview!.version,allowLarge:allowImportLarge})}>Apply previewed text profile</button>
      </div>{/if}
    </section>
    {#if history.length}<section><h4>Recorded revisions</h4><p>Restore creates a new revision. Fork copies the applied profile, not pending edits. Neither rewinds files.</p>
      {#each history.slice(0,30) as h}<div class="history"><ProfileDetails profile={h.data.active} label={`r${h.data.active.revision} · ${h.data.action}`}/><button disabled={waiting||dirty} onclick={()=>action({action:'restore',entry:h.entry,version:expected})}>Restore r{h.data.active.revision}</button><button disabled={waiting||dirty} onclick={()=>action({action:'fork',entry:h.entry})}>Fork at r{h.data.active.revision}</button></div>{/each}
      {#if history.length>30}<p>Showing the latest 30 change entries. Earlier request profiles remain inspectable on their turns.</p>{/if}
    </section>{/if}
  {/if}
  {#if notice}<p role="status">{notice}</p>{/if}{#if error}<p role="alert">{error}</p>{/if}
</div>
<style>
  .agent-profile{padding:18px;font-size:12px;line-height:1.6;color:var(--color-ink)}header,.skill-heading{display:flex;justify-content:space-between;align-items:center;gap:8px}h3,h4{font-weight:500;margin:0}header>span,h4>span{font:10px var(--font-mono);color:var(--color-accent)}h4{margin-top:18px}p{color:var(--color-muted);font-size:11px;margin:9px 0}.revision,.history{display:flex;gap:6px;align-items:center;flex-wrap:wrap;margin:10px 0}fieldset{border:0;padding:0;min-width:0}label{display:block;margin:12px 0}.check{display:flex;align-items:flex-start;gap:8px;font-size:11px}.check input{margin-top:3px}textarea,input:not([type=checkbox]),select{display:block;width:100%;box-sizing:border-box;color:var(--color-ink);background:var(--color-paper-2);border:1px solid var(--color-rule);border-radius:5px;padding:8px;margin-top:6px;font:11px/1.6 var(--font-mono)}textarea{resize:vertical;min-height:60px}button{padding:7px 10px;border:1px solid var(--color-rule);border-radius:5px;background:var(--color-paper-2);color:var(--color-ink);margin:4px 4px 0 0;font-size:11px;cursor:pointer}button:disabled,fieldset:disabled{opacity:.55}button:disabled{cursor:default}.primary{border-color:var(--color-accent);color:var(--color-accent)}.skill{border:1px solid var(--color-rule);padding:10px;margin-top:12px}.skill-heading label{margin:0}.budget{padding:10px;border-left:2px solid var(--color-accent);font-size:11px}.budget small{display:block;color:var(--color-muted);margin-top:6px}.imports{border-top:1px solid var(--color-rule);margin-top:24px}pre{white-space:pre-wrap;overflow-wrap:anywhere;font:10px/1.6 var(--font-mono);max-height:300px;overflow:auto;background:var(--color-paper-2);padding:10px}code{font:10px var(--font-mono);overflow-wrap:anywhere}summary{cursor:pointer;font-size:11px;margin:10px 0}ul{font-size:10px;padding-left:16px}.warning{color:var(--color-accent)}[role=alert]{color:#b34c3b}.import-preview{padding:12px;border:1px solid var(--color-rule);margin-top:12px}
</style>
