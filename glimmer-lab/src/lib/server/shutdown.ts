/** Join owned work on adapter shutdown or signals without replacing adapter-node's handlers. */
type Signals={once(name:string,listener:()=>void):unknown;off(name:string,listener:()=>void):unknown};
export function installShutdown(work:()=>Promise<void>,signals:Signals=process){
  let done:Promise<void>|undefined;
  const stop=()=>done??=Promise.resolve().then(work);
  const notify=()=>{void stop().catch(()=>{console.error('Pi shutdown could not finish; sandbox service deadlines remain in force.');});};
  const names=['SIGTERM','SIGINT','sveltekit:shutdown'];
  for(const name of names)signals.once(name,notify);
  return {stop,dispose(){for(const name of names)signals.off(name,notify);}};
}
