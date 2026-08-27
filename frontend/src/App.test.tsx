import {render,screen,waitFor} from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import {expect,it,vi} from "vitest";
import {App} from "./App";

const question={id:"q1",completion_id:"chatcmpl_server_owned",model:"human-1",status:"claimed",created_at:new Date().toISOString(),expires_at:new Date(Date.now()+60000).toISOString(),claim_expires_at:new Date(Date.now()+60000).toISOString(),is_mine:true,messages:[{position:0,role:"user",content:"<img src=x onerror=alert(1)>"}],answer_content:null};

it("keeps id read-only and submits only content",async()=>{
  const fetchMock=vi.fn(async(input:RequestInfo|URL,init?:RequestInit)=>{
    void init;
    const url=String(input);
    if(url.endsWith("/api/auth/me"))return {ok:true,json:async()=>({user:{id:"u",email:"r@example.test",role:"responder"},csrf_token:"csrf"})};
    if(url.includes("/api/human/questions?"))return {ok:true,json:async()=>({data:[question]})};
    return {ok:true,json:async()=>({ok:true})};
  });
  vi.stubGlobal("fetch",fetchMock);
  Object.assign(navigator,{clipboard:{writeText:vi.fn()}});
  render(<App/>);
  await userEvent.click(await screen.findByRole("button",{name:"Mine"}));
  await screen.findByText("chatcmpl_server_owned");
  await userEvent.click(screen.getByRole("button",{name:"Answer"}));
  const id=screen.getByTestId("human-answer-id");
  expect(id).toHaveAttribute("readonly");
  expect(screen.getByText("<img src=x onerror=alert(1)>")).toBeInTheDocument();
  await userEvent.type(screen.getByTestId("human-answer-content"),'Line 1\n"quoted"');
  await userEvent.click(screen.getByTestId("human-answer-submit"));
  await waitFor(()=>{
    const call=fetchMock.mock.calls.find(([url])=>String(url).includes("/answer"));
    expect(JSON.parse(call?.[1]?.body as string)).toEqual({content:'Line 1\n"quoted"'});
  });
});
