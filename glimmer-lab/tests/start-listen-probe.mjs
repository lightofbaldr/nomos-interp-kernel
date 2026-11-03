// Test-only preload: refuse unsafe test binds BEFORE a socket opens, including
// when exercising the old, faulty npm start command. Never used by the launcher.
import assert from 'node:assert/strict';
import net from 'node:net';
const originalListen=net.Server.prototype.listen;
net.Server.prototype.listen=function(...args){
  const options=args[0],expected=process.env.LAB_START_EXPECT_HOST;
  assert.ok(options && typeof options==='object','Expected adapter-node listen options');
  assert.ok(!options.path && options.fd===undefined,'Socket activation is outside this TCP check');
  assert.equal(options.host,expected,'Unsafe or unexpected bind refused before opening a socket');
  assert.equal(Number(options.port),Number(process.env.PORT));
  if(process.env.LAB_START_CHECK_MODE==='inspect'){
    // Confirm that explicit wider HOST values reach the adapter, without ever
    // opening an interface that could expose the unauthenticated application.
    console.log('START_INTENT '+JSON.stringify({host:options.host,port:Number(options.port)}));
    process.exit(0);
  }
  assert.equal(process.env.LAB_START_CHECK_MODE,'listen');
  assert.equal(expected,'127.0.0.1','Real test sockets are loopback-only');
  this.once('listening',()=>{
    const address=this.address();
    assert.ok(address && typeof address==='object');
    assert.equal(address.address,'127.0.0.1');
    assert.equal(address.family,'IPv4');
    console.log('START_SOCKET '+JSON.stringify(address));
  });
  return originalListen.apply(this,args);
};
