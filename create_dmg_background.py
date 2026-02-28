from PIL import Image, ImageDraw, ImageFont
import sys

# Create background image for DMG (600x400)
img = Image.new('RGB', (600, 400), color='#2b2b2b')
draw = ImageDraw.Draw(img)

# Draw arrow from left to right
arrow_y = 200
draw.line([(250, arrow_y), (350, arrow_y)], fill='#888888', width=3)
# Arrow head
draw.polygon([(350, arrow_y), (340, arrow_y-8), (340, arrow_y+8)], fill='#888888')

# Save
img.save('dmg_background.png')
print("Background created: dmg_background.png")
