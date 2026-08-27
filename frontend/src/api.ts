export type User={id:string;email:string;role:"responder"|"admin"};
export type Message={position:number;role:string;content:string};
export type Question={id:string;completion_id:string;model:string;status:string;created_at:string;expires_at:string;claim_expires_at:string|null;is_mine:boolean;messages:Message[];answer_content:string|null};
const BASE=import.meta.env.VITE_API_BASE_URL??"";
async function parse(response:Response){const body=await response.json();if(!response.ok)throw new Error(body.error?.message??body.detail?.[0]?.msg??`Request failed (${response.status})`);return body;}
export async function login(email:string,password:string){return parse(await fetch(`${BASE}/api/auth/login`,{method:"POST",credentials:"include",headers:{"Content-Type":"application/json"},body:JSON.stringify({email,password})})) as Promise<{user:User;csrf_token:string}>;}
export async function me(){return parse(await fetch(`${BASE}/api/auth/me`,{credentials:"include"})) as Promise<{user:User;csrf_token:string}>;}
export async function list(scope:string){return parse(await fetch(`${BASE}/api/human/questions?scope=${encodeURIComponent(scope)}`,{credentials:"include"})) as Promise<{data:Question[]}>;}
export async function mutate(path:string,csrf:string,body?:object){return parse(await fetch(`${BASE}${path}`,{method:"POST",credentials:"include",headers:{"Content-Type":"application/json","X-CSRF-Token":csrf},body:body?JSON.stringify(body):undefined}));}
export const claim=(id:string,csrf:string)=>mutate(`/api/human/questions/${id}/claim`,csrf);
export const release=(id:string,csrf:string)=>mutate(`/api/human/questions/${id}/release`,csrf);
export const answer=(id:string,content:string,csrf:string)=>mutate(`/api/human/questions/${id}/answer`,csrf,{content});
export const heartbeat=(csrf:string)=>mutate("/api/human/heartbeat",csrf);
