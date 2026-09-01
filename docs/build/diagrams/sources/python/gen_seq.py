import matplotlib; matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.patches import FancyBboxPatch, Rectangle, FancyArrowPatch
NAVY="#0E1A2B"; SLATE="#1F3350"; STEEL="#3E5C82"; AMBER="#E0A030"
GREEN="#2E8B57"; RED="#B3402F"; TEAL="#177E89"; LILAC="#6C5B9E"; GREY="#7E8FA3"

actors=[("Operator",STEEL),("Console\n/ CLI",STEEL),("Core API",NAVY),("GLEIPNIR",RED),
        ("MOTSOGNIR",TEAL),("HAMARR\non node",AMBER),("HODD",GREEN),("RAUN",SLATE),
        ("Ledger",GREEN),("GULLIN-\nBURSTI",LILAC)]
steps=[
 (0,1,"compose run spec, dry run",False),
 (1,2,"POST /v1/runs",False),
 (2,2,"validate schema, hash spec + inputs",True),
 (2,6,"resolve hodd:// refs, verify SHA-256",False),
 (2,3,"licence + residency policy check",False),
 (3,2,"PASS",False),
 (2,8,"DRAFT to QUEUED",False),
 (2,4,"plan job (driver.render, pure)",False),
 (4,5,"sbatch, array %3, one per node",False),
 (5,8,"QUEUED to TRAINING",False),
 (5,2,"progress events (structured)",False),
 (2,1,"server-sent events to run board",False),
 (5,6,"checkpoint write, forced on mains loss",False),
 (5,8,"TRAINING to TRAINED",False),
 (2,7,"dispatch gates E1 to E6",False),
 (7,2,"results bound to artefact SHA-256",False),
 (7,8,"EVALUATING to MERGED, or requeue",False),
 (2,3,"release gate: approval required",False),
 (3,9,"require countersigned anchor",False),
 (9,3,"anchor OK",False),
 (3,8,"AWAITING_APPROVAL to RELEASED",False),
 (2,1,"release package: card, SBOM, lineage,\nArticle 53 summary, sole-approver notice",False),
]
fig,ax=plt.subplots(figsize=(15.5,11.6)); fig.patch.set_facecolor("white")
n=len(actors); xs=[i*1.55 for i in range(n)]
top=len(steps)*0.52+1.1
for (name,col),x in zip(actors,xs):
    ax.add_patch(FancyBboxPatch((x-0.62,top),1.24,0.52,boxstyle="round,pad=0.02,rounding_size=0.08",
        facecolor=col,edgecolor=NAVY,lw=1.2))
    ax.text(x,top+0.26,name,ha="center",va="center",fontsize=8.4,color="white",fontweight="bold")
    ax.plot([x,x],[0.2,top],color=GREY,lw=0.9,ls=(0,(4,3)),zorder=0)
for i,(a,b,label,selfc) in enumerate(steps):
    y=top-0.55-i*0.52
    ax.text(-1.5,y,f"{i+1:>2}",ha="right",va="center",fontsize=7.6,color=GREY)
    if selfc or a==b:
        ax.add_patch(FancyArrowPatch((xs[a],y+0.10),(xs[a],y-0.10),connectionstyle="arc3,rad=-2.6",
            arrowstyle="-|>",mutation_scale=9,color=STEEL,lw=1.1))
        ax.text(xs[a]+0.42,y,label,ha="left",va="center",fontsize=7.6,color=SLATE)
    else:
        col = RED if "requeue" in label or "anchor" in label else STEEL
        ax.add_patch(FancyArrowPatch((xs[a],y),(xs[b],y),arrowstyle="-|>",mutation_scale=10,
            color=col,lw=1.25))
        mx=(xs[a]+xs[b])/2
        ax.text(mx,y+0.13,label,ha="center",va="bottom",fontsize=7.6,color=SLATE)
ax.set_xlim(-2.2,xs[-1]+1.3); ax.set_ylim(0.0,top+1.5); ax.axis("off")
ax.text(-2.1,top+1.25,"DRAUPNIR  ·  SEQUENCE  ·  ADAPTER RUN FROM SUBMISSION TO RELEASE",
        fontsize=15,fontweight="bold",color=NAVY,va="center")
ax.text(-2.1,top+0.92,"Every state transition writes a ledger entry. Release requires both a signed approval and a countersigned federation anchor.",
        fontsize=8.6,color=STEEL,va="center")
fig.tight_layout(); fig.savefig("diagrams/16_sequence.png",dpi=170,facecolor="white",bbox_inches="tight")
print("wrote 16_sequence")
