
import matplotlib.pyplot as plt
import matplotlib.patches as patches

def create_detailed_flow():
    fig, ax = plt.subplots(figsize=(18, 12))
    ax.set_xlim(0, 100)
    ax.set_ylim(0, 100)
    ax.axis('off')

    # Define colors
    color_start = '#ffcc00'
    color_state = '#3399ff'
    color_process = '#99ccff'
    color_ai = '#cc99ff'
    color_user = '#ff6666'
    color_finish = '#66cc66'

    # Helper function to draw boxes
    def draw_box(x, y, text, color, width=12, height=6):
        rect = patches.FancyBboxPatch((x-width/2, y-height/2), width, height, boxstyle="round,pad=0.3", 
                                      linewidth=2, edgecolor='black', facecolor=color, alpha=0.9)
        ax.add_patch(rect)
        ax.text(x, y, text, ha='center', va='center', fontsize=9, fontweight='bold', wrap=True)

    # 1. Start & Trigger
    draw_box(10, 90, "START\nTelegram Link/File", color_start)
    
    # 2. Waiting for Title State
    draw_box(30, 90, "STATE:\nWAITING_FOR_TITLE", color_state)
    
    # 3. Processing Pipeline
    draw_box(50, 90, "DOWNLOAD_MODULES\nVideo + Sidecar Metadata", color_process)
    draw_box(70, 90, "CLAW_VANGUARD\nMission Control\n(Repair/Cleaning)", color_ai)
    draw_box(90, 90, "VISION_INTEL\nNiche Detection\n(Fashion/NSFW/etc)", color_ai)

    # 4. Content Refinement
    draw_box(90, 70, "HYBRID_WATERMARK\nDetection & Removal", color_process)
    draw_box(70, 70, "MONETIZATION_BRAIN\nLink Lookup (JSON/AI)\nDynamic Hook Gen", color_ai)
    draw_box(50, 70, "THUMB_MODULES\nAuto-Thumbnail Gen", color_process)

    # 5. Waiting for Approval
    draw_box(30, 70, "STATE:\nWAITING_FOR_APPROVAL", color_state)
    draw_box(10, 70, "USER INTERFACE\nAdmin Report +\nApprove/Reject Buttons", color_user)

    # 6. Title Expansion & Affiliate Link Step (CRITICAL NUANCE)
    draw_box(10, 50, "USER ACTION\nClick 'Approve & Post'", color_user)
    draw_box(30, 50, "STATE:\nWAITING_FOR_EXPANSION", color_state)
    
    draw_box(55, 50, "INPUT PARSER\n'index [aff_link] [mrp]'\nDetect Link Type (Space count)", color_process, width=20)
    
    # 7. Storage & Assembly
    draw_box(85, 50, "STORAGE\nAuto-save to JSON\n(Metrics/Affiliate_link.json)", color_finish)
    draw_box(85, 30, "CAPTION_MODULES\nAssemble Dual Funnel\n(Viral Hook + Shop CTA)", color_process)

    # 8. Final Delivery
    draw_box(60, 30, "PERFORM_UPLOAD\nInject Affiliate Link\nPost to YT/IG/TG", color_finish)
    draw_box(35, 30, "DEDUPLICATION\nRegister Content UID\n(vid_xxxxxx)", color_process)
    draw_box(10, 30, "CLEANUP\nStrict Deletion of\nArtifacts/Buffers", color_finish)

    # Draw Arrows
    def arrow(x1, y1, x2, y2):
        ax.annotate('', xy=(x2, y2), xytext=(x1, y1),
                    arrowprops=dict(facecolor='black', shrink=0.05, width=1, headwidth=8))

    # Row 1
    arrow(16, 90, 24, 90)
    arrow(36, 90, 44, 90)
    arrow(56, 90, 64, 90)
    arrow(76, 90, 84, 90)
    
    # Transition to Row 2
    arrow(90, 87, 90, 73)
    
    # Row 2
    arrow(84, 70, 76, 70)
    arrow(64, 70, 56, 70)
    arrow(44, 70, 36, 70)
    arrow(24, 70, 16, 70)
    
    # Transition to Row 3
    arrow(10, 67, 10, 53)
    
    # Row 3
    arrow(16, 50, 24, 50)
    arrow(36, 50, 45, 50)
    arrow(65, 50, 79, 50)
    
    # Transition to Row 4
    arrow(85, 47, 85, 33)
    
    # Row 4
    arrow(79, 30, 66, 30)
    arrow(54, 30, 41, 30)
    arrow(29, 30, 16, 30)

    plt.title("AMTCE Autonomous Engine - Deep Flow Architecture v3", fontsize=16, fontweight='bold', pad=20)
    plt.tight_layout()
    plt.savefig('amtce_deep_flow_v3.png', dpi=300, bbox_inches='tight')
    print("Graph generated: amtce_deep_flow_v3.png")

if __name__ == "__main__":
    create_detailed_flow()
