# VHS tape for generating gd-audit terminal demo GIF
# Requirements: charmbracelet/vhs (https://github.com/charmbracelet/vhs)
# Usage: vhs demo.tape

Output demo.gif

Set Shell "bash"
Set FontSize 16
Set Width 1280
Set Height 720
Set Padding 24
Set Theme "Catppuccin Mocha"
Set WindowBar Colorful
Set TypingSpeed 40ms

# Initial greeting and instant run with uvx
Sleep 1s
Type "# Instant architecture audit without installing:"
Enter
Sleep 600ms
Type "uvx gd-audit ."
Sleep 400ms
Enter

# Allow output table and graph validation to render
Sleep 4.5s

# Run focused severity check for CI
Type "# Check with custom failure threshold for CI gates:"
Enter
Sleep 600ms
Type "gd-audit . --fail-on high --severity warning"
Sleep 400ms
Enter

# Display final results
Sleep 4s

# Clear and complete demo loop
Sleep 2s
