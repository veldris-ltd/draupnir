#!/usr/bin/env python3
"""Render JARNGREIPR token sheet, component state matrix and console wireframes."""
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.patches import FancyBboxPatch, Rectangle, Circle

NAVY="#0E1A2B"; SLATE="#1F3350"; STEEL="#3E5C82"; GREY="#7E8FA3"
LINE="#B9C6D4"; ICE="#E9F0F7"; PAPER="#F5F8FB"; AMBER="#E0A030"
GREEN="#2E8B57"; RED="#B3402F"; TEAL="#177E89"; LILAC="#6C5B9E"
plt.rcParams["font.family"]="DejaVu Sans"

# ------------------------------------------------------------------ helpers
def frame(ax,x,y,w,h,title=None,fill="white",edge=LINE,lw=1.1,r=0.10,tc=NAVY,ts=8.0):
    ax.add_patch(FancyBboxPatch((x,y),w,h,boxstyle=f"round,pad=0.01,rounding_size={r}",
                 facecolor=fill,edgecolor=edge,lw=lw,zorder=2))
    if title:
        ax.text(x+0.22,y+h-0.28,title,fontsize=ts,fontweight="bold",color=tc,va="center",zorder=4)

def bar(ax,x,y,w,h,fill=LINE,edge=None,r=0.06,z=3):
    ax.add_patch(FancyBboxPatch((x,y),w,h,boxstyle=f"round,pad=0.005,rounding_size={r}",
                 facecolor=fill,edgecolor=edge or fill,lw=0.7,zorder=z))

def txt(ax,x,y,s,size=6.6,color=SLATE,weight="normal",ha="left",va="center",z=4,mono=False):
    ax.text(x,y,s,fontsize=size,color=color,fontweight=weight,ha=ha,va=va,zorder=z,
            family="DejaVu Sans Mono" if mono else "DejaVu Sans")

def pill(ax,x,y,label,fill,tc="white",w=None,size=5.8):
    w = w or (0.055*len(label)+0.28)
    bar(ax,x,y-0.115,w,0.23,fill=fill,r=0.115)
    txt(ax,x+w/2,y,label,size=size,color=tc,weight="bold",ha="center")
    return w

def skel(ax,x,y,w,n=3,gap=0.20,h=0.09,shrink=0.72):
    for i in range(n):
        bar(ax,x,y-i*gap,w*(1 if i==0 else shrink),h,fill=LINE)

def newfig(w,h,title,sub):
    fig,ax=plt.subplots(figsize=(w,h)); fig.patch.set_facecolor("white")
    ax.axis("off")
    ax.text(0,0,"",fontsize=1)
    return fig,ax

def header(ax,x,y,title,sub):
    txt(ax,x,y,title,size=15,color=NAVY,weight="bold")
    txt(ax,x,y-0.42,sub,size=8.2,color=STEEL)

# ------------------------------------------------------------- token sheet
def tokens(path):
    fig,ax=plt.subplots(figsize=(15.4,10.6)); fig.patch.set_facecolor("white"); ax.axis("off")
    ax.set_xlim(0,15.4); ax.set_ylim(0,10.6)
    header(ax,0.3,10.2,"JARNGREIPR  ·  DESIGN TOKENS","Veldris Ltd  ·  the only source of visual values. A hard coded value in a component fails the token linter.")

    ramps=[("ink (base)",["#0E1A2B","#1F3350","#2A4A6B","#3E5C82","#7E8FA3","#B9C6D4","#E9F0F7","#F5F8FB"],
            ["900","800","700","600","400","300","100","50"]),
           ("forge (accent)",["#8A5A08","#C6851C","#E0A030","#EFC272","#FBEFD6","#FDF7EC"],
            ["800","700","500","300","100","50"]),
           ("success",["#1B5E3A","#2E8B57","#7CC49B","#EAF4EE"],["800","500","300","50"]),
           ("warning",["#8A5A08","#D98E04","#F0C15C","#FBF3E0"],["800","500","300","50"]),
           ("danger",["#7A2A1F","#B3402F","#DC8C80","#FBF2F0"],["800","500","300","50"]),
           ("info",["#0F5760","#177E89","#7DBFC6","#F2FAFB"],["800","500","300","50"])]
    y=9.4
    txt(ax,0.3,y+0.18,"COLOUR",size=9,color=NAVY,weight="bold")
    for name,cols,labels in ramps:
        y-=0.62
        txt(ax,0.3,y+0.16,name,size=7.2,color=SLATE,weight="bold")
        for i,(c,l) in enumerate(zip(cols,labels)):
            x=2.05+i*0.82
            bar(ax,x,y-0.14,0.76,0.46,fill=c,edge=LINE)
            txt(ax,x+0.38,y+0.22,l,size=5.8,color="white" if i<len(cols)-3 else NAVY,ha="center",weight="bold")
            txt(ax,x+0.38,y-0.28,c,size=5.2,color=GREY,ha="center",mono=True)

    # run state semantics
    y-=0.95
    txt(ax,0.3,y+0.2,"RUN STATE SEMANTICS",size=9,color=NAVY,weight="bold")
    states=[("QUEUED",STEEL),("TRAINING",AMBER),("TRAINED","#EFC272"),("EVALUATING",SLATE),
            ("MERGED",LILAC),("QUANTISED",NAVY),("AWAITING",  "#D98E04"),("RELEASED",GREEN),
            ("FAILED",RED),("QUARANTINED","#7A2A1F")]
    x=2.05
    for s,c in states:
        w=pill(ax,x,y-0.12,s,c,tc=NAVY if s=="TRAINED" else "white")
        x+=w+0.14

    # typography
    y-=0.85
    txt(ax,0.3,y+0.2,"TYPE SCALE",size=9,color=NAVY,weight="bold")
    txt(ax,0.3,y-0.12,"Inter (UI)  ·  JetBrains Mono (hashes, code, logs)",size=6.6,color=GREY)
    scale=[("display","32/40",16),("h1","24/32",13),("h2","20/28",11),("h3","16/24",9.2),
           ("body","14/22",8.0),("small","13/20",7.2),("caption","12/16",6.4),("mono","13/20",7.2)]
    x=2.05
    for name,spec,sz in scale:
        txt(ax,x,y+0.12,"Ag",size=sz,color=NAVY,weight="bold",mono=(name=="mono"))
        txt(ax,x,y-0.32,name,size=5.8,color=SLATE,weight="bold")
        txt(ax,x,y-0.52,spec,size=5.2,color=GREY,mono=True)
        x+=1.62

    # spacing / radius / elevation / motion
    y-=1.15
    txt(ax,0.3,y+0.2,"SPACE  ·  RADIUS  ·  ELEVATION  ·  MOTION",size=9,color=NAVY,weight="bold")
    x=2.05
    for v in [4,8,12,16,24,32,48,64]:
        bar(ax,x,y-0.16,v/64*0.62,0.34,fill=STEEL)
        txt(ax,x,y-0.36,str(v),size=5.4,color=GREY,mono=True)
        x+=0.80
    x+=0.25
    for r,lab in [(0.03,"2"),(0.07,"4"),(0.13,"8"),(0.20,"12"),(0.30,"pill")]:
        bar(ax,x,y-0.16,0.56,0.34,fill=ICE,edge=LINE,r=r)
        txt(ax,x+0.28,y+0.01,lab,size=5.6,color=SLATE,ha="center")
        x+=0.72
    x+=0.25
    for i,(lab,sh) in enumerate([("e0 flat","none"),("e1 card","0 1 2"),("e2 overlay","0 8 24")]):
        bar(ax,x,y-0.16,0.86,0.34,fill="white",edge=LINE)
        txt(ax,x+0.43,y+0.01,lab,size=5.4,color=SLATE,ha="center")
        x+=1.02
    x+=0.2
    txt(ax,x,y+0.06,"micro 120 ms   standard 200 ms   large 320 ms",size=6.0,color=SLATE)
    txt(ax,x,y-0.20,"ease cubic-bezier(0.2, 0, 0, 1)   ·   respects prefers-reduced-motion",size=6.0,color=GREY)

    # density
    y-=0.95
    txt(ax,0.3,y+0.2,"DENSITY MODES",size=9,color=NAVY,weight="bold")
    frame(ax,2.05,y-0.62,5.6,0.90,fill="white")
    txt(ax,2.25,y+0.10,"COMFORTABLE  ·  default  ·  desktop browser",size=7.0,color=NAVY,weight="bold")
    txt(ax,2.25,y-0.16,"row height 44 px  ·  body 14 px  ·  gutter 24 px",size=6.2,color=GREY)
    txt(ax,2.25,y-0.42,"used by operators, curators, approvers and auditors at a workstation",size=6.0,color=GREY)
    frame(ax,7.95,y-0.62,5.6,0.90,fill=PAPER)
    txt(ax,8.15,y+0.10,"COMPACT  ·  CON-A and CON-B  ·  9 inch 1280x720 touch",size=7.0,color=NAVY,weight="bold")
    txt(ax,8.15,y-0.16,"row height 32 px  ·  body 13 px  ·  gutter 12 px  ·  44 px touch targets",size=6.2,color=GREY)
    txt(ax,8.15,y-0.42,"read-mostly. No composition tasks at this size.",size=6.0,color=GREY)

    fig.tight_layout(); fig.savefig(path,dpi=175,facecolor="white",bbox_inches="tight"); plt.close(fig)
    print("wrote",path)

# ------------------------------------------------------- state matrix
def state_matrix(path):
    fig,ax=plt.subplots(figsize=(15.2,7.4)); fig.patch.set_facecolor("white"); ax.axis("off")
    ax.set_xlim(0,15.2); ax.set_ylim(0,7.4)
    header(ax,0.3,7.0,"JARNGREIPR  ·  UNIVERSAL COMPONENT STATES",
           "Every component ships all six. A component with only a happy path is not complete and fails AC-U2.")
    states=[("LOADING","skeleton, no spinner for content;\nspinner only for actions under 1 s",ICE,NAVY),
            ("EMPTY","says what would be here, and the\none action that creates it",PAPER,NAVY),
            ("ERROR","problem title, what the user can do,\ncopyable correlation id",'#FBF2F0',RED),
            ("DENIED","names the role required, not\n'forbidden'. Offers who to ask",'#FBF3E0','#8A5A08'),
            ("READ ONLY","states why: ledger divergence,\nauditor role, or archived run",'#F2FAFB',TEAL),
            ("PARTITIONED","site is cut off from MEGINGJORD.\nTraining continues, release disabled",'#F4F1FA',LILAC)]
    x=0.3
    for name,desc,fill,tc in states:
        frame(ax,x,3.55,2.38,2.85,fill=fill)
        txt(ax,x+0.20,6.10,name,size=8.4,color=tc,weight="bold")
        bar(ax,x+0.20,5.90,1.98,0.02,fill=LINE)
        for i,l in enumerate(desc.split("\n")):
            txt(ax,x+0.20,5.62-i*0.24,l,size=6.2,color=SLATE)
        # mini mock
        if name=="LOADING": skel(ax,x+0.20,4.85,1.98,3)
        elif name=="EMPTY":
            txt(ax,x+1.19,4.90,"No runs yet",size=6.6,color=GREY,ha="center")
            pill(ax,x+0.72,4.55,"Submit a run",STEEL,w=0.95)
        elif name=="ERROR":
            txt(ax,x+0.20,4.95,"Specification invalid",size=6.4,color=RED,weight="bold")
            txt(ax,x+0.20,4.72,"dataset hash does not match",size=5.8,color=SLATE)
            txt(ax,x+0.20,4.48,"corr: 01J9F2K...",size=5.6,color=GREY,mono=True)
        elif name=="DENIED":
            txt(ax,x+0.20,4.90,"Requires role: approver",size=6.4,color="#8A5A08",weight="bold")
            txt(ax,x+0.20,4.62,"You hold: operator",size=5.8,color=SLATE)
        elif name=="READ ONLY":
            txt(ax,x+0.20,4.90,"Read only",size=6.4,color=TEAL,weight="bold")
            txt(ax,x+0.20,4.62,"ledger verification failed",size=5.8,color=SLATE)
        else:
            txt(ax,x+0.20,4.90,"Site partitioned",size=6.4,color=LILAC,weight="bold")
            txt(ax,x+0.20,4.62,"last anchor 3 h 12 m ago",size=5.8,color=SLATE)
            txt(ax,x+0.20,4.38,"Release unavailable",size=5.8,color=RED)
        x+=2.50

    frame(ax,0.3,0.4,14.6,2.75,fill="white")
    txt(ax,0.55,2.85,"CONTENT RULES",size=8.6,color=NAVY,weight="bold")
    rules=[
      "British English throughout. Sentence case for labels and headings; no title case, no ALL CAPS except state pills.",
      "Errors name the thing that failed and the action available. Never 'Something went wrong'. Never an unexplained code alone.",
      "Empty states describe what belongs there and offer the single action that creates it.",
      "Denied states name the role required and the role held. 'Forbidden' is not an explanation.",
      "Numbers carry units. Durations are human ('3 h 12 m'), hashes are truncated to 8 characters with copy-to-clipboard.",
      "Destructive confirmations state the consequence in words, not 'Are you sure?'. Typing the artefact name is required for release withdrawal.",
      "Terminology is fixed: site is a forge, node is an appliance, rank is a position in a job. Never interchange them.",
      "The sole approver notice reads as a statement of record, not a warning. It is a disclosed fact, not an error.",
    ]
    for i,r in enumerate(rules):
        txt(ax,0.55,2.55-i*0.27,"·  "+r,size=6.9,color=SLATE)
    fig.tight_layout(); fig.savefig(path,dpi=175,facecolor="white",bbox_inches="tight"); plt.close(fig)
    print("wrote",path)

# ------------------------------------------------------------ wireframes
def shell(ax,x,y,w,h,active,site="SINDRI  ·  Site 0",compact=False):
    """Console shell chrome. Returns content rect (cx,cy,cw,ch)."""
    frame(ax,x,y,w,h,fill="white",edge=STEEL,lw=1.4)
    # top bar
    bar(ax,x+0.04,y+h-0.42,w-0.08,0.38,fill=NAVY,r=0.06)
    txt(ax,x+0.20,y+h-0.23,"DRAUPNIR",size=7.2,color="white",weight="bold")
    txt(ax,x+1.35,y+h-0.23,site,size=6.0,color="#B9C6D4")
    txt(ax,x+w-2.55,y+h-0.23,"⌘K  search and commands",size=5.6,color="#B9C6D4")
    bar(ax,x+w-0.60,y+h-0.34,0.22,0.22,fill=AMBER,r=0.11)
    txt(ax,x+w-0.30,y+h-0.23,"AK",size=5.4,color="#B9C6D4")
    # side nav
    nav=["Overview","Corpora","Runs","Models","Gates","Admin","Audit"]
    navw=1.30
    for i,n in enumerate(nav):
        yy=y+h-0.75-i*0.30
        if n==active:
            bar(ax,x+0.08,yy-0.11,navw,0.26,fill=ICE,r=0.06)
            bar(ax,x+0.08,yy-0.11,0.05,0.26,fill=AMBER,r=0.02)
        txt(ax,x+0.26,yy,n,size=6.4,color=NAVY if n==active else STEEL,
            weight="bold" if n==active else "normal")
    return (x+navw+0.18, y+0.10, w-navw-0.30, h-0.62)

def wire_runs(path):
    import numpy as np
    fig,ax=plt.subplots(figsize=(15.4,9.0)); fig.patch.set_facecolor("white"); ax.axis("off")
    ax.set_xlim(0,15.4); ax.set_ylim(0,9.0)
    header(ax,0.3,8.6,"DRAUPNIR CONSOLE  ·  WIREFRAMES 1  ·  RUN BOARD AND RUN DETAIL",
           "Journey J2, Operate. Low fidelity: layout, hierarchy and state only. Visual values come from the token sheet.")

    # ---- run board
    cx,cy,cw,ch = shell(ax,0.3,4.35,7.2,3.65,"Runs")
    txt(ax,cx+0.10,cy+ch-0.16,"Runs",size=8.4,color=NAVY,weight="bold")
    pill(ax,cx+cw-1.05,cy+ch-0.16,"Submit run",AMBER,tc=NAVY,w=0.95)
    for i,f in enumerate(["All states","Tier A","Tier B","This site"]):
        bar(ax,cx+0.10+i*1.02,cy+ch-0.52,0.94,0.20,fill=PAPER,edge=LINE,r=0.10)
        txt(ax,cx+0.57+i*1.02,cy+ch-0.42,f,size=5.2,color=STEEL,ha="center")
    cols=[("JURIS",0.10),("BASE",0.90),("STATE",2.25),("PROGRESS",3.40),("NODE",4.62),("ELAPSED",5.15)]
    for c,off in cols: txt(ax,cx+off,cy+ch-0.78,c,size=5.0,color=GREY,weight="bold")
    bar(ax,cx+0.06,cy+ch-0.86,cw-0.16,0.012,fill=LINE)
    rows=[("GBR","QWEN36-27B","TRAINING",AMBER,0.62,"DVALIN","11h04"),
          ("IND","QWEN36-27B","TRAINING",AMBER,0.31,"DURIN","5h21"),
          ("CAN","QWEN36-27B","EVALUATING",SLATE,0.88,"DAIN","2h06"),
          ("NGA","QWEN36-27B","QUEUED",STEEL,0.0,"—","—"),
          ("MLT","QWEN36-35B","FAILED",RED,0.44,"DURIN","1h12"),
          ("ZAF","QWEN36-27B","RELEASED",GREEN,1.0,"—","13h40")]
    for i,(j,b,s_,c,p,n,e) in enumerate(rows):
        yy=cy+ch-1.08-i*0.31
        if i%2==0: bar(ax,cx+0.06,yy-0.13,cw-0.16,0.27,fill=PAPER,r=0.04)
        txt(ax,cx+0.10,yy,j,size=6.0,color=NAVY,weight="bold")
        txt(ax,cx+0.90,yy,b,size=5.4,color=SLATE,mono=True)
        pill(ax,cx+2.25,yy,s_,c,w=1.02,size=4.8)
        bar(ax,cx+3.40,yy-0.05,1.05,0.10,fill=LINE,r=0.05)
        if p>0: bar(ax,cx+3.40,yy-0.05,1.05*p,0.10,fill=c,r=0.05)
        txt(ax,cx+4.62,yy,n,size=5.4,color=SLATE)
        txt(ax,cx+5.15,yy,e,size=5.4,color=SLATE,mono=True)
    txt(ax,cx+0.10,cy+0.10,"live  ·  server-sent events  ·  updated 2 s ago",size=5.2,color=GREEN)

    # ---- run detail
    cx,cy,cw,ch = shell(ax,7.9,4.35,7.2,3.65,"Runs")
    txt(ax,cx+0.10,cy+ch-0.16,"MIDGARD-CIM-GBR-QWEN36-27B-v0.3",size=7.0,color=NAVY,weight="bold")
    pill(ax,cx+cw-1.05,cy+ch-0.16,"Cancel run",RED,w=0.95)
    pill(ax,cx+0.10,cy+ch-0.48,"TRAINING",AMBER,w=0.90,size=5.0)
    txt(ax,cx+1.12,cy+ch-0.48,"DVALIN · rank 0 · step 4,120 / 6,600 · 6 h 12 m left",size=5.4,color=SLATE)
    for i,t in enumerate(["Overview","Spec","Logs","Gates","Lineage","Ledger"]):
        bar(ax,cx+0.10+i*0.92,cy+ch-0.92,0.86,0.21,fill=ICE if i==0 else "white",edge=LINE,r=0.05)
        txt(ax,cx+0.53+i*0.92,cy+ch-0.815,t,size=5.2,color=NAVY if i==0 else STEEL,ha="center",
            weight="bold" if i==0 else "normal")
    frame(ax,cx+0.10,cy+0.95,2.85,1.12,fill=PAPER)
    txt(ax,cx+0.24,cy+1.90,"Training loss",size=5.8,color=SLATE,weight="bold")
    xs=np.linspace(cx+0.28,cx+2.82,60); ys=cy+1.12+0.62*np.exp(-np.linspace(0,3,60))+0.015*np.random.RandomState(3).randn(60)
    ax.plot(xs,ys,color=AMBER,lw=1.1,zorder=4)
    frame(ax,cx+3.10,cy+0.95,cw-3.25,1.12,fill="white")
    stats=[("Spec hash","a41f9c2e…"),("Base","QWEN36-27B  Apache-2.0"),
           ("Dataset","GBR/curated  7bd3…"),("Driver","hamarr.llamafactory/v1"),
           ("Checkpoint","every 210 steps")]
    for i,(k,v) in enumerate(stats):
        txt(ax,cx+3.24,cy+1.88-i*0.21,k,size=5.2,color=GREY)
        txt(ax,cx+4.10,cy+1.88-i*0.21,v,size=5.2,color=SLATE,mono=True)
    frame(ax,cx+0.10,cy+0.08,cw-0.25,0.76,fill="#F7F9FB")
    txt(ax,cx+0.24,cy+0.70,"Log tail  ·  virtualised  ·  200,000 lines",size=5.4,color=SLATE,weight="bold")
    for i,l in enumerate(["{'loss': 1.412, 'lr': 8.1e-05, 'step': 4118}",
                          "{'loss': 1.408, 'lr': 8.0e-05, 'step': 4119}",
                          "[checkpoint] wrote step 4120  ·  62.4 GB"]):
        txt(ax,cx+0.24,cy+0.50-i*0.16,l,size=4.9,color=STEEL,mono=True)

    # ---- failure detail
    cx,cy,cw,ch = shell(ax,0.3,0.35,14.8,3.55,"Runs")
    txt(ax,cx+0.10,cy+ch-0.16,"MIDGARD-CIM-MLT-QWEN36-35B-A3B-v0.1",size=7.2,color=NAVY,weight="bold")
    pill(ax,cx+cw-1.10,cy+ch-0.16,"Retry run",AMBER,tc=NAVY,w=0.95)
    pill(ax,cx+cw-2.15,cy+ch-0.16,"Diagnose",STEEL,w=0.90)
    pill(ax,cx+0.10,cy+ch-0.50,"FAILED",RED,w=0.78,size=5.0)
    txt(ax,cx+1.02,cy+ch-0.50,"retry 1 of 2 available  ·  DURIN  ·  failed at step 1,842 after 1 h 12 m",size=5.4,color=SLATE)
    frame(ax,cx+0.10,cy+1.32,cw-0.25,0.98,fill="#FBF2F0",edge="#DC8C80")
    txt(ax,cx+0.28,cy+2.12,"Out of memory during the backward pass",size=7.0,color=RED,weight="bold")
    txt(ax,cx+0.28,cy+1.88,"A sequence of 14,208 tokens exceeded the configured cutoff of 8,192. This is a dataset property, not a configuration error.",size=6.0,color=SLATE)
    txt(ax,cx+0.28,cy+1.66,"Suggested action:  set cutoff_len to the 99th percentile (9,120) and enable length grouped batching.",size=6.0,color=NAVY,weight="bold")
    txt(ax,cx+0.28,cy+1.45,"correlation  01J9F2K7QW3E  ·  copy",size=5.4,color=GREY,mono=True)
    frame(ax,cx+0.10,cy+0.08,7.05,1.14,fill=PAPER)
    txt(ax,cx+0.26,cy+1.06,"Token length distribution  ·  from the dataset that produced this failure",size=5.6,color=SLATE,weight="bold")
    rs=np.random.RandomState(7); h=np.histogram(rs.lognormal(8.1,0.55,4000),bins=26)[0]
    for i,v in enumerate(h):
        bar(ax,cx+0.28+i*0.255,cy+0.32,0.21,0.62*v/h.max(),fill=STEEL if i<22 else RED,r=0.02)
    txt(ax,cx+0.28,cy+0.18,"p50 3,290        p99 9,120        max 14,208",size=5.2,color=GREY,mono=True)
    frame(ax,cx+7.35,cy+0.08,cw-7.50,1.14,fill="white")
    txt(ax,cx+7.51,cy+1.06,"Ledger entries for this run",size=5.6,color=SLATE,weight="bold")
    for i,(t,s_) in enumerate([("11:04:02","DRAFT to QUEUED"),("11:04:19","QUEUED to TRAINING"),
                              ("12:16:41","TRAINING to FAILED"),("12:16:41","retry budget 2 to 1")]):
        yy=cy+0.86-i*0.21
        bar(ax,cx+7.40,yy-0.03,0.06,0.06,fill=GREEN,r=0.03)
        txt(ax,cx+7.53,yy,t,size=5.2,color=GREY,mono=True)
        txt(ax,cx+8.40,yy,s_,size=5.4,color=SLATE)
    fig.tight_layout(); fig.savefig(path,dpi=175,facecolor="white",bbox_inches="tight"); plt.close(fig)
    print("wrote",path)

def wire_gates(path):
    fig,ax=plt.subplots(figsize=(15.4,10.35)); fig.patch.set_facecolor("white"); ax.axis("off")
    ax.set_xlim(0,15.4); ax.set_ylim(0,10.35)
    header(ax,0.3,10.08,"DRAUPNIR CONSOLE  ·  WIREFRAMES 2  ·  GATE QUEUE AND APPROVAL",
           "Journey J3, Approve. Evidence sits above the decision control, so approving requires seeing the results.")

    cx,cy,cw,ch = shell(ax,0.3,5.15,6.6,4.40,"Gates")
    txt(ax,cx+0.10,cy+ch-0.16,"Approval queue",size=8.4,color=NAVY,weight="bold")
    txt(ax,cx+0.10,cy+ch-0.44,"4 artefacts awaiting decision  ·  oldest 2 d 04 h",size=5.8,color=STEEL)
    items=[("GBR","QWEN36-27B-v1.0","6 / 6 pass",GREEN,"2 d 04 h"),
           ("CAN","QWEN36-27B-v1.0","6 / 6 pass",GREEN,"1 d 11 h"),
           ("AUS","QWEN36-27B-v0.9","5 / 6  E4 margin 1.8 pp","#D98E04","18 h"),
           ("SGP","QWEN36-27B-v1.0","6 / 6 pass",GREEN,"6 h")]
    for i,(j,m,g,c,age) in enumerate(items):
        yy=cy+ch-1.02-i*0.66
        frame(ax,cx+0.08,yy-0.24,cw-0.22,0.56,fill="white" if i else ICE)
        txt(ax,cx+0.26,yy+0.14,j+"  ·  "+m,size=6.4,color=NAVY,weight="bold")
        pill(ax,cx+0.26,yy-0.11,g,c,w=1.62,size=5.0)
        txt(ax,cx+2.05,yy-0.11,"waiting "+age,size=5.4,color=GREY)
        pill(ax,cx+cw-1.18,yy+0.02,"Review",STEEL,w=0.94)

    cx,cy,cw,ch = shell(ax,7.30,5.15,7.8,4.40,"Gates")
    txt(ax,cx+0.10,cy+ch-0.16,"MIDGARD-CIM-GBR-QWEN36-27B-v1.0",size=7.2,color=NAVY,weight="bold")
    txt(ax,cx+0.10,cy+ch-0.42,"artefact 9c41f8ba…  ·  merged, TIES, weight 0.65  ·  NVFP4, GGUF Q4_K_M, MLX4 all re-gated",size=5.5,color=STEEL)
    txt(ax,cx+0.10,cy+ch-0.72,"EVIDENCE",size=6.0,color=NAVY,weight="bold")
    for c,off in [("GATE",0.12),("VALUE",2.90),("BASELINE",3.85),("MARGIN",4.85),("RESULT",5.60)]:
        txt(ax,cx+off,cy+ch-0.94,c,size=5.0,color=GREY,weight="bold")
    gates=[("E1 legal citation","0.914","0.902","+1.2 pp"),
           ("E2 jurisdiction","0.961","—","≥ 0.90"),
           ("E3 language","—","—","n/a EN only"),
           ("E4 capability retention","0.782","0.791","−0.9 pp"),
           ("E5 refusal and safety","0.997","0.996","+0.1 pp"),
           ("E6 hallucinated authority","0.021","0.024","−0.3 pp")]
    for i,(g,v,b,m) in enumerate(gates):
        yy=cy+ch-1.16-i*0.245
        if i%2==0: bar(ax,cx+0.08,yy-0.105,cw-0.22,0.23,fill=PAPER,r=0.04)
        txt(ax,cx+0.12,yy,g,size=5.6,color=SLATE)
        txt(ax,cx+2.90,yy,v,size=5.6,color=NAVY,mono=True,weight="bold")
        txt(ax,cx+3.85,yy,b,size=5.6,color=GREY,mono=True)
        txt(ax,cx+4.85,yy,m,size=5.6,color=GREEN,mono=True)
        pill(ax,cx+5.60,yy,"PASS",GREEN,w=0.58,size=4.7)
    frame(ax,cx+0.10,cy+0.60,cw-0.25,0.74,fill="#FBF3E0",edge="#F0C15C")
    txt(ax,cx+0.28,cy+1.16,"Sole approver, recorded",size=6.3,color="#8A5A08",weight="bold")
    txt(ax,cx+0.28,cy+0.93,"You submitted this run and you are approving it. Separation of duties is not available at Site 0.",size=5.6,color=SLATE)
    txt(ax,cx+0.28,cy+0.73,"This release will carry sole_approver_exception in its lineage and model card. Acceptance VLD-RA-SINDRI-001.",size=5.6,color=SLATE)
    pill(ax,cx+cw-3.40,cy+0.26,"Reject with reason",RED,w=1.58)
    pill(ax,cx+cw-1.72,cy+0.26,"Sign and approve",GREEN,w=1.58)

    cx,cy,cw,ch = shell(ax,0.3,0.40,7.2,4.45,"Gates")
    txt(ax,cx+0.10,cy+ch-0.16,"Publish release",size=8.0,color=NAVY,weight="bold")
    checks=["Approval signed","Gates E1 to E6 pass on merged artefact",
            "Gates re-run on NVFP4, GGUF and MLX","Model card rendered",
            "CycloneDX SBOM generated","SHA-256 manifest written",
            "Article 53 training summary generated","Copyright policy v3 referenced",
            "Federation anchor countersigned"]
    for i,c in enumerate(checks):
        yy=cy+ch-0.62-i*0.29
        ax.add_patch(Circle((cx+0.22,yy),0.075,facecolor=GREEN,edgecolor="none",zorder=4))
        txt(ax,cx+0.40,yy,c,size=6.0,color=SLATE)
    pill(ax,cx+cw-1.62,cy+0.28,"Publish to registry",NAVY,w=1.52)

    cx,cy,cw,ch = shell(ax,7.90,0.40,7.2,4.45,"Gates")
    frame(ax,cx+0.10,cy+ch-1.40,cw-0.25,1.24,fill="#F4F1FA",edge=LILAC)
    txt(ax,cx+0.30,cy+ch-0.44,"This site is partitioned from the Forge Matrix",size=7.0,color=LILAC,weight="bold")
    txt(ax,cx+0.30,cy+ch-0.70,"Last successful anchor to MEGINGJORD: 3 h 12 m ago. Training, evaluation and merging continue normally.",size=5.8,color=SLATE)
    txt(ax,cx+0.30,cy+ch-0.94,"Release requires a countersigned anchor, so publication is unavailable until the link is restored.",size=5.8,color=SLATE)
    txt(ax,cx+0.30,cy+ch-1.20,"4 artefacts are held in AWAITING_APPROVAL. None has been lost.",size=5.8,color=NAVY,weight="bold")
    bar(ax,cx+0.10,cy+ch-1.84,cw-0.25,0.32,fill="#EFEFF2",r=0.06)
    txt(ax,cx+0.30,cy+ch-1.68,"Publish to registry",size=6.2,color=GREY,weight="bold")
    txt(ax,cx+2.35,cy+ch-1.68,"disabled  ·  reason stated above, not a generic failure",size=5.5,color=GREY)
    txt(ax,cx+0.10,cy+1.15,"Design rule",size=6.6,color=NAVY,weight="bold")
    txt(ax,cx+0.10,cy+0.88,"An action that cannot succeed is disabled with the reason",size=6.1,color=SLATE)
    txt(ax,cx+0.10,cy+0.66,"stated in place, never left enabled to fail. AC-U12.",size=6.1,color=SLATE)
    txt(ax,cx+0.10,cy+0.34,"The same rule governs denied, read only and loading.",size=6.1,color=GREY)
    fig.tight_layout(); fig.savefig(path,dpi=175,facecolor="white",bbox_inches="tight"); plt.close(fig)
    print("wrote",path)

def wire_lineage(path):
    fig,ax=plt.subplots(figsize=(15.4,10.4)); fig.patch.set_facecolor("white"); ax.axis("off")
    ax.set_xlim(0,15.4); ax.set_ylim(0,10.4)
    header(ax,0.3,10.12,"DRAUPNIR CONSOLE  ·  WIREFRAMES 3  ·  LINEAGE, SWEEP AND CORPUS",
           "Journeys J4 Audit and J1 Curate. A complete lineage must be reachable in three interactions or fewer.")

    # lineage explorer
    cx,cy,cw,ch = shell(ax,0.3,5.30,7.5,4.25,"Audit")
    txt(ax,cx+0.10,cy+ch-0.16,"Lineage  ·  MIDGARD-CIM-GBR-QWEN36-27B-v1.0",size=7.0,color=NAVY,weight="bold")
    pill(ax,cx+cw-1.32,cy+ch-0.16,"Export attestation",STEEL,w=1.24)
    nodes=[(0,2.90,"RELEASE  v1.0",NAVY,"white","9c41f8ba · signed · anchored"),
           (1,2.48,"MERGED  TIES w=0.65",LILAC,"white","sweep of 5 · selected"),
           (2,2.06,"ADAPTER  lora r=64",AMBER,NAVY,"run 4f2a · DVALIN · 13 h"),
           (3,1.64,"SUBSTRATE  CORE v1.0",TEAL,"white","ring run · 3 nodes · 6 d"),
           (4,1.22,"BASE  Qwen3.6-27B",GREEN,"white","Apache-2.0 · 7bd3…"),
           (4,0.80,"CORPUS  GBR curated",STEEL,"white","41.2 M tok · 12 sources")]
    for ind,yy,label,c,tc,sub in nodes:
        x0=cx+0.12+ind*0.30
        ax.plot([x0-0.14,x0-0.14],[cy+yy+0.14,cy+yy+0.40],color=LINE,lw=1.0,zorder=1)
        bar(ax,x0,cy+yy,2.32,0.28,fill=c,r=0.06)
        txt(ax,x0+0.10,cy+yy+0.14,label,size=5.6,color=tc,weight="bold")
        txt(ax,cx+3.85,cy+yy+0.14,sub,size=5.3,color=GREY)
    frame(ax,cx+0.10,cy+0.20,cw-0.25,0.38,fill="#EAF4EE",edge=GREEN)
    txt(ax,cx+0.26,cy+0.39,"Chain complete. 0 gaps. Ledger verified to sequence 41,208 at 12:44.",size=5.9,color=GREEN,weight="bold")

    # sweep matrix
    cx,cy,cw,ch = shell(ax,8.20,5.30,6.9,4.25,"Models")
    txt(ax,cx+0.10,cy+ch-0.16,"Reweight sweep  ·  GBR  ·  TIES",size=7.0,color=NAVY,weight="bold")
    txt(ax,cx+0.10,cy+ch-0.42,"Five merge points. The selected point is recorded in the model card.",size=5.6,color=STEEL)
    ws=["0.45","0.55","0.65","0.75","0.85"]
    for i,w in enumerate(ws):
        txt(ax,cx+1.91+i*0.76,cy+ch-0.72,"w="+w,size=5.5,color=GREY,weight="bold",ha="center")
    rows=[("E1 citation",[0.881,0.899,0.914,0.921,0.925],False),
          ("E2 jurisdiction",[0.918,0.944,0.961,0.968,0.971],False),
          ("E4 retention",[0.798,0.791,0.782,0.759,0.731],True),
          ("E6 fabrication",[0.019,0.020,0.021,0.028,0.041],True)]
    for r,(name,vals,inv) in enumerate(rows):
        yy=cy+ch-1.02-r*0.42
        txt(ax,cx+0.12,yy,name,size=5.7,color=SLATE)
        for i,v in enumerate(vals):
            good = (v>=0.771) if name=="E4 retention" else ((v<=0.025) if inv else True)
            col = "#EAF4EE" if good else "#FBF2F0"
            bar(ax,cx+1.55+i*0.76,yy-0.15,0.72,0.31,fill=col,edge=LINE,r=0.04)
            txt(ax,cx+1.91+i*0.76,yy,f"{v:.3f}",size=5.4,color=NAVY if good else RED,ha="center",mono=True)
    ax.add_patch(FancyBboxPatch((cx+3.05,cy+ch-2.44),0.76,1.58,
        boxstyle="round,pad=0.005,rounding_size=0.04",facecolor="none",edgecolor=AMBER,lw=1.6,zorder=6))
    txt(ax,cx+1.91+2*0.76,cy+ch-2.60,"selected",size=5.2,color=AMBER,ha="center",weight="bold")
    txt(ax,cx+0.12,cy+0.62,"w=0.75 and w=0.85 score higher on E1 and E2 but breach the",size=5.8,color=SLATE)
    txt(ax,cx+0.12,cy+0.40,"E4 retention floor of 0.771.",size=5.8,color=SLATE)
    txt(ax,cx+0.12,cy+0.16,"The console states the trade rather than presenting one number.",size=5.8,color=NAVY,weight="bold")

    # corpus registration
    cx,cy,cw,ch = shell(ax,0.3,0.40,9.2,4.55,"Corpora")
    txt(ax,cx+0.10,cy+ch-0.16,"Register source  ·  GBR",size=7.8,color=NAVY,weight="bold")
    txt(ax,cx+0.10,cy+ch-0.42,"Step 2 of 4  ·  licence and data protection",size=5.7,color=STEEL)
    for i in range(4):
        bar(ax,cx+0.10+i*0.60,cy+ch-0.60,0.52,0.06,fill=AMBER if i<2 else LINE,r=0.03)
    fields=[("Source URL","https://www.legislation.gov.uk/",False),
            ("Licence (SPDX)","OGL-UK-3.0",False),
            ("Attribution required","Yes  ·  reproduced in every model card",False),
            ("Contains personal data","Yes",True),
            ("DPIA reference","VLD-DPIA-GBR-004",True),
            ("Residency constraint","none  ·  may be processed at any site",False)]
    for i,(k,v,flag) in enumerate(fields):
        yy=cy+ch-1.02-i*0.42
        txt(ax,cx+0.12,yy+0.13,k,size=5.5,color=GREY)
        bar(ax,cx+0.12,yy-0.21,4.05,0.28,fill="white",edge="#F0C15C" if flag else LINE,r=0.05)
        txt(ax,cx+0.24,yy-0.07,v,size=5.6,color=SLATE,mono=True)
    frame(ax,cx+4.45,cy+1.35,3.15,2.15,fill="#FBF3E0",edge="#F0C15C")
    txt(ax,cx+4.62,cy+3.28,"Data protection gate",size=6.4,color="#8A5A08",weight="bold")
    for i,l in enumerate(["You have declared that this source contains","personal data. A DPIA reference is required",
                          "before curation can run, and the reference is","recorded in the corpus register."]):
        txt(ax,cx+4.62,cy+3.02-i*0.21,l,size=5.6,color=SLATE)
    for i,l in enumerate(["Legal corpora are dense with named individuals,","so this gate applies to most jurisdictions",
                          "rather than a minority."]):
        txt(ax,cx+4.62,cy+2.10-i*0.21,l,size=5.6,color=GREY)
    pill(ax,cx+cw-3.10,cy+0.28,"Back",STEEL,w=1.42)
    pill(ax,cx+cw-1.58,cy+0.28,"Continue",AMBER,tc=NAVY,w=1.46)

    # CON-A local view
    frame(ax,9.85,0.40,5.25,4.55,fill=NAVY,edge=STEEL,lw=1.6)
    txt(ax,10.05,4.68,"CON-A  ·  local console on DVALIN  ·  compact density",size=6.5,color="white",weight="bold")
    txt(ax,10.05,4.46,"read only  ·  reads the local appliance  ·  works when the API is unreachable",size=5.3,color="#7E8FA3")
    lines=[("GPU","GB10   62 °C   util 97 %   118.2 / 128 GB",GREEN),
           ("THROTTLE","none",GREEN),
           ("FABRIC","4 devices Up   200000 Mb/s",GREEN),
           ("RING","192.168.0.1 / .2.1   peers reachable",GREEN),
           ("RUN","cim-adapter GBR   4120 / 6600   11 h 04 m",AMBER),
           ("VAULT","hodd 2.71 / 4.00 TB   nfs rtt 0.4 ms",GREEN),
           ("SCHEDULER","unreachable   ·   REGIN not responding",RED),
           ("API","unreachable   ·   local state only",RED)]
    for i,(k,v,c) in enumerate(lines):
        yy=4.05-i*0.34
        bar(ax,10.05,yy-0.03,0.06,0.17,fill=c,r=0.03)
        txt(ax,10.22,yy+0.06,k,size=5.7,color="#B9C6D4",weight="bold",mono=True)
        txt(ax,11.30,yy+0.06,v,size=5.4,color="white",mono=True)
    txt(ax,10.05,1.14,"This view exists because the DGX Spark has no baseboard",size=5.4,color="#7E8FA3")
    txt(ax,10.05,0.94,"management controller. It is the only console that survives",size=5.4,color="#7E8FA3")
    txt(ax,10.05,0.74,"a total network failure, and the reason CON-A is driven by",size=5.4,color="#7E8FA3")
    txt(ax,10.05,0.54,"DVALIN rather than by REGIN. Decision D12.",size=5.4,color="#7E8FA3")
    fig.tight_layout(); fig.savefig(path,dpi=175,facecolor="white",bbox_inches="tight"); plt.close(fig)
    print("wrote",path)

if __name__=="__main__":
    tokens("diagrams/20_tokens.png")
    state_matrix("diagrams/21_states.png")
    wire_runs("diagrams/22_wire_runs.png")
    wire_gates("diagrams/23_wire_gates.png")
    wire_lineage("diagrams/24_wire_lineage.png")
