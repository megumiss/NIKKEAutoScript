import{P as J,b as ue,c as he,W as le,h as Te}from"./h264-1fb6061e.js";class Ee{#e=[];constructor(){this.dispose=this.dispose.bind(this)}addDisposable(r){return this.#e.push(r),r}dispose(){for(const r of this.#e)r.dispose();this.#e=[]}}class Q{listeners=[];constructor(){this.event=this.event.bind(this)}addEventListener(r){this.listeners.push(r);const i=()=>{const a=this.listeners.indexOf(r);a!==-1&&this.listeners.splice(a,1)};return i.dispose=i,i}event=(r,i,...a)=>{const n={listener:r,thisArg:i,args:a};return this.addEventListener(n)};fire(r){for(const i of this.listeners.slice())i.listener.call(i.thisArg,r,...i.args)}dispose(){this.listeners.length=0}}const K=Symbol("undefined");class de extends Q{#e=K;addEventListener(r){return this.#e!==K&&r.listener.call(r.thisArg,this.#e,...r.args),super.addEventListener(r)}fire(r){this.#e=r,super.fire(r)}}function Z(t){return t&&t.__esModule&&Object.prototype.hasOwnProperty.call(t,"default")?t.default:t}var ee={validateDimension:function(t){if(t<=0||t!==(t|0))throw"YUV plane dimensions must be a positive integer"},validateOffset:function(t){if(t<0||t!==(t|0))throw"YUV plane offsets must be a non-negative integer"},format:function(t){var r=t.width,i=t.height,a=t.chromaWidth||r,n=t.chromaHeight||i,e=t.cropLeft||0,h=t.cropTop||0,l=t.cropWidth||r-e,T=t.cropHeight||i-h,x=t.displayWidth||l,E=t.displayHeight||T;return this.validateDimension(r),this.validateDimension(i),this.validateDimension(a),this.validateDimension(n),this.validateOffset(e),this.validateOffset(h),this.validateDimension(l),this.validateDimension(T),this.validateDimension(x),this.validateDimension(E),{width:r,height:i,chromaWidth:a,chromaHeight:n,cropLeft:e,cropTop:h,cropWidth:l,cropHeight:T,displayWidth:x,displayHeight:E}},suitableStride:function(t){ee.validateDimension(t);var r=4,i=t%r;return i==0?t:t+(r-i)},allocPlane:function(t,r,i,a,n){var e,h;if(this.validateDimension(t),this.validateDimension(r),n=n||0,a=a||this.suitableStride(t),this.validateDimension(a),a<t)throw"Invalid input stride for YUV plane; must be larger than width";if(e=a*r,i){if(i.length-n<e)throw"Invalid input buffer for YUV plane; must be large enough for stride times height";h=i.slice(n,n+e)}else h=new Uint8Array(e),a=a||this.suitableStride(t);return{bytes:h,stride:a}},lumaPlane:function(t,r,i,a){return this.allocPlane(t.width,t.height,r,i,a)},chromaPlane:function(t,r,i,a){return this.allocPlane(t.chromaWidth,t.chromaHeight,r,i,a)},frame:function(t,r,i,a){return r=r||this.lumaPlane(t),i=i||this.chromaPlane(t),a=a||this.chromaPlane(t),{format:t,y:r,u:i,v:a}},copyPlane:function(t){return{bytes:t.bytes.slice(),stride:t.stride}},copyFrame:function(t){return{format:t.format,y:this.copyPlane(t.y),u:this.copyPlane(t.u),v:this.copyPlane(t.v)}},transferables:function(t){return[t.y.bytes.buffer,t.u.bytes.buffer,t.v.bytes.buffer]}},fe=ee;const N=Z(fe);var te={exports:{}},re={exports:{}};(function(){function t(r,i){throw new Error("abstract")}t.prototype.drawFrame=function(r){throw new Error("abstract")},t.prototype.clear=function(){throw new Error("abstract")},re.exports=t})();var $=re.exports,ie={exports:{}},ae={exports:{}},ne={exports:{}};(function(){/**
 * Convert a ratio into a bit-shift count; for instance a ratio of 2
 * becomes a bit-shift of 1, while a ratio of 1 is a bit-shift of 0.
 *
 * @author Brooke Vibber <bvibber@pobox.com>
 * @copyright 2016-2024
 * @license MIT-style
 *
 * @param {number} ratio - the integer ratio to convert.
 * @returns {number} - number of bits to shift to multiply/divide by the ratio.
 * @throws exception if given a non-power-of-two
 */function t(r){for(var i=0,a=r>>1;a!=0;)a=a>>1,i++;if(r!==1<<i)throw"chroma plane dimensions must be power of 2 ratio to luma plane dimensions; got "+r;return i}ne.exports=t})();var pe=ne.exports;(function(){var t=pe;/**
 * Basic YCbCr->RGB conversion
 *
 * @author Brooke Vibber <bvibber@pobox.com>
 * @copyright 2014-2024
 * @license MIT-style
 *
 * @param {YUVFrame} buffer - input frame buffer
 * @param {Uint8ClampedArray} output - array to draw RGBA into
 * Assumes that the output array already has alpha channel set to opaque.
 */function r(i,a){var n=i.format.width|0,e=i.format.height|0,h=t(i.format.width/i.format.chromaWidth)|0,l=t(i.format.height/i.format.chromaHeight)|0,T=i.y.bytes,x=i.u.bytes,E=i.v.bytes,R=i.y.stride|0,m=i.u.stride|0,u=i.v.stride|0,v=n<<2,f=0,A=0,Y=0,X=0,S=0,y=0,P=0,_=0,w=0,U=0,d=0,b=0,L=0,D=0,C=0,o=0,s=0,c=0;if(h==1&&l==1)for(P=0,_=v,c=0,o=0;o<e;o+=2){for(A=o*R|0,Y=A+R|0,X=c*m|0,S=c*u|0,C=0;C<n;C+=2)w=x[X++]|0,U=E[S++]|0,b=(409*U|0)-57088|0,L=(100*w|0)+(208*U|0)-34816|0,D=(516*w|0)-70912|0,d=298*T[A++]|0,a[P]=d+b>>8,a[P+1]=d-L>>8,a[P+2]=d+D>>8,P+=4,d=298*T[A++]|0,a[P]=d+b>>8,a[P+1]=d-L>>8,a[P+2]=d+D>>8,P+=4,d=298*T[Y++]|0,a[_]=d+b>>8,a[_+1]=d-L>>8,a[_+2]=d+D>>8,_+=4,d=298*T[Y++]|0,a[_]=d+b>>8,a[_+1]=d-L>>8,a[_+2]=d+D>>8,_+=4;P+=v,_+=v,c++}else for(y=0,o=0;o<e;o++)for(s=0,c=o>>l,f=o*R|0,X=c*m|0,S=c*u|0,C=0;C<n;C++)s=C>>h,w=x[X+s]|0,U=E[S+s]|0,b=(409*U|0)-57088|0,L=(100*w|0)+(208*U|0)-34816|0,D=(516*w|0)-70912|0,d=298*T[f++]|0,a[y]=d+b>>8,a[y+1]=d-L>>8,a[y+2]=d+D>>8,y+=4}ae.exports={convertYCbCr:r}})();var me=ae.exports;(function(){var t=$,r=me;function i(a){var n=this,e=a.getContext("2d"),h=null,l=null,T=null;function x(R,m){h=e.createImageData(R,m);for(var u=h.data,v=R*m*4,f=0;f<v;f+=4)u[f+3]=255}function E(R,m){l=document.createElement("canvas"),l.width=R,l.height=m,T=l.getContext("2d")}return n.drawFrame=function(m){var u=m.format;(a.width!==u.displayWidth||a.height!==u.displayHeight)&&(a.width=u.displayWidth,a.height=u.displayHeight),(h===null||h.width!=u.width||h.height!=u.height)&&x(u.width,u.height),r.convertYCbCr(m,h.data);var v=u.cropWidth!=u.displayWidth||u.cropHeight!=u.displayHeight,f;v?(l||E(u.cropWidth,u.cropHeight),f=T):f=e,f.putImageData(h,-u.cropLeft,-u.cropTop,u.cropLeft,u.cropTop,u.cropWidth,u.cropHeight),v&&e.drawImage(l,0,0,u.displayWidth,u.displayHeight)},n.clear=function(){e.clearRect(0,0,a.width,a.height)},n}i.prototype=Object.create(t.prototype),ie.exports=i})();var ve=ie.exports,oe={exports:{}},Re={vertex:`precision mediump float;

attribute vec2 aPosition;
attribute vec2 aLumaPosition;
attribute vec2 aChromaPosition;
varying vec2 vLumaPosition;
varying vec2 vChromaPosition;
void main() {
    gl_Position = vec4(aPosition, 0, 1);
    vLumaPosition = aLumaPosition;
    vChromaPosition = aChromaPosition;
}
`,fragment:`// inspired by https://github.com/mbebenita/Broadway/blob/master/Player/canvas.js

precision mediump float;

uniform sampler2D uTextureY;
uniform sampler2D uTextureCb;
uniform sampler2D uTextureCr;
varying vec2 vLumaPosition;
varying vec2 vChromaPosition;
void main() {
   // Y, Cb, and Cr planes are uploaded as ALPHA textures.
   float fY = texture2D(uTextureY, vLumaPosition).w;
   float fCb = texture2D(uTextureCb, vChromaPosition).w;
   float fCr = texture2D(uTextureCr, vChromaPosition).w;

   // Premultipy the Y...
   float fYmul = fY * 1.1643828125;

   // And convert that to RGB!
   gl_FragColor = vec4(
     fYmul + 1.59602734375 * fCr - 0.87078515625,
     fYmul - 0.39176171875 * fCb - 0.81296875 * fCr + 0.52959375,
     fYmul + 2.017234375   * fCb - 1.081390625,
     1
   );
}
`,vertexStripe:`precision mediump float;

attribute vec2 aPosition;
attribute vec2 aTexturePosition;
varying vec2 vTexturePosition;

void main() {
    gl_Position = vec4(aPosition, 0, 1);
    vTexturePosition = aTexturePosition;
}
`,fragmentStripe:`// extra 'stripe' texture fiddling to work around IE 11's poor performance on gl.LUMINANCE and gl.ALPHA textures

precision mediump float;

uniform sampler2D uStripe;
uniform sampler2D uTexture;
varying vec2 vTexturePosition;
void main() {
   // Y, Cb, and Cr planes are mapped into a pseudo-RGBA texture
   // so we can upload them without expanding the bytes on IE 11
   // which doesn't allow LUMINANCE or ALPHA textures
   // The stripe textures mark which channel to keep for each pixel.
   // Each texture extraction will contain the relevant value in one
   // channel only.

   float fLuminance = dot(
      texture2D(uStripe, vTexturePosition),
      texture2D(uTexture, vTexturePosition)
   );

   gl_FragColor = vec4(0, 0, 0, fLuminance);
}
`};(function(){var t=$,r=Re;function i(a){var n=this,e=i.contextForCanvas(a);if(e===null)throw new Error("WebGL unavailable");function h(o,s){var c=e.createShader(o);if(e.shaderSource(c,s),e.compileShader(c),!e.getShaderParameter(c,e.COMPILE_STATUS)){var p=e.getShaderInfoLog(c);throw e.deleteShader(c),new Error("GL shader compilation for "+o+" failed: "+p)}return c}var l,T,x=new Float32Array([-1,-1,1,-1,-1,1,-1,1,1,-1,1,1]),E={},R={},m={},u,v,f,A,Y,X,S,y,P,_;function w(o,s){return(!E[o]||s)&&(E[o]=e.createTexture()),E[o]}function U(o,s,c,p,g){var F=!E[o]||s,W=w(o,s);if(e.activeTexture(e.TEXTURE0),i.stripe){var I=!E[o+"_temp"]||s,H=w(o+"_temp",s);e.bindTexture(e.TEXTURE_2D,H),I?(e.texParameteri(e.TEXTURE_2D,e.TEXTURE_WRAP_S,e.CLAMP_TO_EDGE),e.texParameteri(e.TEXTURE_2D,e.TEXTURE_WRAP_T,e.CLAMP_TO_EDGE),e.texParameteri(e.TEXTURE_2D,e.TEXTURE_MIN_FILTER,e.NEAREST),e.texParameteri(e.TEXTURE_2D,e.TEXTURE_MAG_FILTER,e.NEAREST),e.texImage2D(e.TEXTURE_2D,0,e.RGBA,c/4,p,0,e.RGBA,e.UNSIGNED_BYTE,g)):e.texSubImage2D(e.TEXTURE_2D,0,0,0,c/4,p,e.RGBA,e.UNSIGNED_BYTE,g);var B=E[o+"_stripe"],G=!B||s;G&&(B=w(o+"_stripe",s)),e.bindTexture(e.TEXTURE_2D,B),G&&(e.texParameteri(e.TEXTURE_2D,e.TEXTURE_WRAP_S,e.CLAMP_TO_EDGE),e.texParameteri(e.TEXTURE_2D,e.TEXTURE_WRAP_T,e.CLAMP_TO_EDGE),e.texParameteri(e.TEXTURE_2D,e.TEXTURE_MIN_FILTER,e.NEAREST),e.texParameteri(e.TEXTURE_2D,e.TEXTURE_MAG_FILTER,e.NEAREST),e.texImage2D(e.TEXTURE_2D,0,e.RGBA,c,1,0,e.RGBA,e.UNSIGNED_BYTE,L(c)))}else e.bindTexture(e.TEXTURE_2D,W),F?(e.texParameteri(e.TEXTURE_2D,e.TEXTURE_WRAP_S,e.CLAMP_TO_EDGE),e.texParameteri(e.TEXTURE_2D,e.TEXTURE_WRAP_T,e.CLAMP_TO_EDGE),e.texParameteri(e.TEXTURE_2D,e.TEXTURE_MIN_FILTER,e.LINEAR),e.texParameteri(e.TEXTURE_2D,e.TEXTURE_MAG_FILTER,e.LINEAR),e.texImage2D(e.TEXTURE_2D,0,e.ALPHA,c,p,0,e.ALPHA,e.UNSIGNED_BYTE,g)):e.texSubImage2D(e.TEXTURE_2D,0,0,0,c,p,e.ALPHA,e.UNSIGNED_BYTE,g)}function d(o,s,c,p){var g=E[o];e.useProgram(T);var F=R[o];(!F||s)&&(e.activeTexture(e.TEXTURE0),e.bindTexture(e.TEXTURE_2D,g),e.texParameteri(e.TEXTURE_2D,e.TEXTURE_WRAP_S,e.CLAMP_TO_EDGE),e.texParameteri(e.TEXTURE_2D,e.TEXTURE_WRAP_T,e.CLAMP_TO_EDGE),e.texParameteri(e.TEXTURE_2D,e.TEXTURE_MIN_FILTER,e.LINEAR),e.texParameteri(e.TEXTURE_2D,e.TEXTURE_MAG_FILTER,e.LINEAR),e.texImage2D(e.TEXTURE_2D,0,e.RGBA,c,p,0,e.RGBA,e.UNSIGNED_BYTE,null),F=R[o]=e.createFramebuffer()),e.bindFramebuffer(e.FRAMEBUFFER,F),e.framebufferTexture2D(e.FRAMEBUFFER,e.COLOR_ATTACHMENT0,e.TEXTURE_2D,g,0);var W=E[o+"_temp"];e.activeTexture(e.TEXTURE1),e.bindTexture(e.TEXTURE_2D,W),e.uniform1i(X,1);var I=E[o+"_stripe"];e.activeTexture(e.TEXTURE2),e.bindTexture(e.TEXTURE_2D,I),e.uniform1i(Y,2),e.bindBuffer(e.ARRAY_BUFFER,u),e.enableVertexAttribArray(v),e.vertexAttribPointer(v,2,e.FLOAT,!1,0,0),e.bindBuffer(e.ARRAY_BUFFER,f),e.enableVertexAttribArray(A),e.vertexAttribPointer(A,2,e.FLOAT,!1,0,0),e.viewport(0,0,c,p),e.drawArrays(e.TRIANGLES,0,x.length/2),e.bindFramebuffer(e.FRAMEBUFFER,null)}function b(o,s,c){e.activeTexture(s),e.bindTexture(e.TEXTURE_2D,E[o]),e.texParameteri(e.TEXTURE_2D,e.TEXTURE_WRAP_S,e.CLAMP_TO_EDGE),e.texParameteri(e.TEXTURE_2D,e.TEXTURE_WRAP_T,e.CLAMP_TO_EDGE),e.texParameteri(e.TEXTURE_2D,e.TEXTURE_MIN_FILTER,e.LINEAR),e.texParameteri(e.TEXTURE_2D,e.TEXTURE_MAG_FILTER,e.LINEAR),e.uniform1i(e.getUniformLocation(l,o),c)}function L(o){if(m[o])return m[o];for(var s=o,c=new Uint32Array(s),p=0;p<s;p+=4)c[p]=255,c[p+1]=65280,c[p+2]=16711680,c[p+3]=4278190080;return m[o]=new Uint8Array(c.buffer)}function D(o,s){var c=h(e.VERTEX_SHADER,o),p=h(e.FRAGMENT_SHADER,s),g=e.createProgram();if(e.attachShader(g,c),e.attachShader(g,p),e.linkProgram(g),!e.getProgramParameter(g,e.LINK_STATUS)){var F=e.getProgramInfoLog(g);throw e.deleteProgram(g),new Error("GL program linking failed: "+F)}return g}function C(){if(i.stripe){T=D(r.vertexStripe,r.fragmentStripe),e.getAttribLocation(T,"aPosition"),f=e.createBuffer();var o=new Float32Array([0,0,1,0,0,1,0,1,1,0,1,1]);e.bindBuffer(e.ARRAY_BUFFER,f),e.bufferData(e.ARRAY_BUFFER,o,e.STATIC_DRAW),A=e.getAttribLocation(T,"aTexturePosition"),Y=e.getUniformLocation(T,"uStripe"),X=e.getUniformLocation(T,"uTexture")}l=D(r.vertex,r.fragment),u=e.createBuffer(),e.bindBuffer(e.ARRAY_BUFFER,u),e.bufferData(e.ARRAY_BUFFER,x,e.STATIC_DRAW),v=e.getAttribLocation(l,"aPosition"),S=e.createBuffer(),y=e.getAttribLocation(l,"aLumaPosition"),P=e.createBuffer(),_=e.getAttribLocation(l,"aChromaPosition")}return n.drawFrame=function(o){var s=o.format,c=!l||a.width!==s.displayWidth||a.height!==s.displayHeight;if(c&&(a.width=s.displayWidth,a.height=s.displayHeight,n.clear()),l||C(),c){var p=function(g,F,W){var I=s.cropLeft/W,H=(s.cropLeft+s.cropWidth)/W,B=(s.cropTop+s.cropHeight)/s.height,G=s.cropTop/s.height,ce=new Float32Array([I,B,H,B,I,G,I,G,H,B,H,G]);e.bindBuffer(e.ARRAY_BUFFER,g),e.bufferData(e.ARRAY_BUFFER,ce,e.STATIC_DRAW)};p(S,y,o.y.stride),p(P,_,o.u.stride*s.width/s.chromaWidth)}U("uTextureY",c,o.y.stride,s.height,o.y.bytes),U("uTextureCb",c,o.u.stride,s.chromaHeight,o.u.bytes),U("uTextureCr",c,o.v.stride,s.chromaHeight,o.v.bytes),i.stripe&&(d("uTextureY",c,o.y.stride,s.height),d("uTextureCb",c,o.u.stride,s.chromaHeight),d("uTextureCr",c,o.v.stride,s.chromaHeight)),e.useProgram(l),e.viewport(0,0,a.width,a.height),b("uTextureY",e.TEXTURE0,0),b("uTextureCb",e.TEXTURE1,1),b("uTextureCr",e.TEXTURE2,2),e.bindBuffer(e.ARRAY_BUFFER,u),e.enableVertexAttribArray(v),e.vertexAttribPointer(v,2,e.FLOAT,!1,0,0),e.bindBuffer(e.ARRAY_BUFFER,S),e.enableVertexAttribArray(y),e.vertexAttribPointer(y,2,e.FLOAT,!1,0,0),e.bindBuffer(e.ARRAY_BUFFER,P),e.enableVertexAttribArray(_),e.vertexAttribPointer(_,2,e.FLOAT,!1,0,0),e.drawArrays(e.TRIANGLES,0,x.length/2)},n.clear=function(){e.viewport(0,0,a.width,a.height),e.clearColor(0,0,0,0),e.clear(e.COLOR_BUFFER_BIT)},n.clear(),n}i.stripe=!1,i.contextForCanvas=function(a){var n={preferLowPowerToHighPerformance:!0,powerPreference:"low-power",failIfMajorPerformanceCaveat:!0,preserveDrawingBuffer:!0};return a.getContext("webgl",n)||a.getContext("experimental-webgl",n)},i.isAvailable=function(){var a=document.createElement("canvas"),n;a.width=1,a.height=1;try{n=i.contextForCanvas(a)}catch{return!1}if(n){var e=n.TEXTURE0,h=4,l=4,T=n.createTexture(),x=new Uint8Array(h*l),E=i.stripe?h/4:h,R=i.stripe?n.RGBA:n.ALPHA,m=i.stripe?n.NEAREST:n.LINEAR;n.activeTexture(e),n.bindTexture(n.TEXTURE_2D,T),n.texParameteri(n.TEXTURE_2D,n.TEXTURE_WRAP_S,n.CLAMP_TO_EDGE),n.texParameteri(n.TEXTURE_2D,n.TEXTURE_WRAP_T,n.CLAMP_TO_EDGE),n.texParameteri(n.TEXTURE_2D,n.TEXTURE_MIN_FILTER,m),n.texParameteri(n.TEXTURE_2D,n.TEXTURE_MAG_FILTER,m),n.texImage2D(n.TEXTURE_2D,0,R,E,l,0,R,n.UNSIGNED_BYTE,x);var u=n.getError();return!u}else return!1},i.prototype=Object.create(t.prototype),oe.exports=i})();var ge=oe.exports;(function(){var t=$,r=ve,i=ge,a={FrameSink:t,SoftwareFrameSink:r,WebGLFrameSink:i,attach:function(n,e){e=e||{};var h="webGL"in e?e.webGL:i.isAvailable();return h?new i(n,e):new r(n,e)}};te.exports=a})();var xe=te.exports;const _e=Z(xe);let M,q=!1;const k=[];let O=0;const V=new Map;function Pe(t,r){return V.set(t,r),{dispose(){V.delete(t)}}}class j extends Ee{streamId;#e=new Q;get onPictureReady(){return this.#e.event}constructor(r){super(),this.streamId=r,this.addDisposable(Pe(r,this.#r))}#r=r=>{this.#e.fire(r)};feed(r){M.postMessage({type:"decode",data:r,offset:0,length:r.byteLength,renderStateId:this.streamId},[r])}dispose(){super.dispose(),M.postMessage({type:"release",renderStateId:this.streamId})}}function se(){if(M||(M=new Worker(new URL("/app/assets/worker-d39a2aa9.js",self.location),{type:"module"}),M.addEventListener("message",({data:r})=>{switch(r.type){case"decoderReady":q=!0;for(const i of k)i.resolve(new j(O)),O+=1;k.length=0;break;case"pictureReady":V.get(r.renderStateId)?.(r);break}})),!q){const r=new J;return k.push(r),r.promise}const t=new j(O);return O+=1,Promise.resolve(t)}const Ae=()=>{};function z(){if(typeof document<"u")return document.createElement("canvas");if(typeof OffscreenCanvas<"u")return new OffscreenCanvas(1,1);throw new Error("no canvas input found nor any canvas can be created")}class be{static capabilities={h264:{maxProfile:ue.Baseline,maxLevel:he.Level4}};#e;get renderer(){return this.#e}#r=new de;get sizeChanged(){return this.#r.event}#a=0;get width(){return this.#a}#n=0;get height(){return this.#n}#o=0;get framesRendered(){return this.#o}#c=0;get framesSkipped(){return this.#c}#s;get writable(){return this.#s}#i;#t;constructor({canvas:r}={}){r?this.#e=r:this.#e=z(),this.#s=new le({write:async i=>{switch(i.type){case"configuration":await this.#u(i.data);break;case"data":{if(!this.#t)throw new Error("Decoder not configured");(await this.#t.promise).feed(i.data.slice().buffer);break}}}})}async#u(r){if(this.dispose(),this.#t=new J,!this.#i){const v=z(),f={failIfMajorPerformanceCaveat:!0},A=v.getContext("webgl2",f)||v.getContext("webgl",f);this.#i=_e.attach(this.#e,{webGL:!!A})}const{encodedWidth:i,encodedHeight:a,croppedWidth:n,croppedHeight:e,cropLeft:h,cropTop:l}=Te(r);this.#a=n,this.#n=e,this.#r.fire({width:n,height:e});const T=i/2,x=a/2,E=N.format({width:i,height:a,chromaWidth:T,chromaHeight:x,cropLeft:h,cropTop:l,cropWidth:n,cropHeight:e,displayWidth:n,displayHeight:e}),R=await se();this.#t.resolve(R);const m=i*a,u=m+T*x;R.onPictureReady(({data:v})=>{this.#o+=1;const f=new Uint8Array(v),A=N.frame(E,N.lumaPlane(E,f,i,0),N.chromaPlane(E,f,T,m),N.chromaPlane(E,f,T,u));this.#i.drawFrame(A)}),R.feed(r.slice().buffer)}dispose(){this.#t?.promise.then(r=>r.dispose()).catch(Ae),this.#t=void 0}}const we=Object.freeze(Object.defineProperty({__proto__:null,TinyH264Decoder:be,TinyH264Wrapper:j,createCanvas:z,createTinyH264Wrapper:se},Symbol.toStringTag,{value:"Module"}));export{de as S,z as c,we as i};
