#!/bin/bash
# WAF-7: Comprehensive app name cleanup script

echo "🧹 WAF-7: Cleaning up old app name references..."

# Files to exclude from replacements (to avoid breaking imports/git history)
EXCLUDE_DIRS="--exclude-dir=.git --exclude-dir=__pycache__ --exclude-dir=.pytest_cache --exclude-dir=dist --exclude-dir=build"
EXCLUDE_FILES="--exclude=name_cleanup.sh --exclude=WAF-7-changes.md"

# Function to replace text in all files matching pattern
replace_text() {
    local old_text="$1"
    local new_text="$2" 
    local description="$3"
    
    echo "  → Replacing '$old_text' with '$new_text' ($description)"
    
    # Find files containing the old text
    grep -rl $EXCLUDE_DIRS $EXCLUDE_FILES "$old_text" . | while read -r file; do
        # Skip binary files and compiled files
        if [[ "$file" =~ \.(pyc|pyo|exe|dmg|icns|ico|png|jpg|jpeg|gif|svg)$ ]]; then
            continue
        fi
        
        # Perform replacement
        if sed -i '' "s|$old_text|$new_text|g" "$file" 2>/dev/null; then
            echo "    ✓ Updated: $file"
        fi
    done
}

# Replace VoiceFlow references (case-sensitive)
replace_text "VoiceFlow" "Waffler" "main app name"

# Replace voiceflow references (lowercase)
replace_text "voiceflow" "waffler" "lowercase app name"

# Replace voice_flow references
replace_text "voice_flow" "waffler" "snake_case app name"

# Replace voice-flow references  
replace_text "voice-flow" "waffler" "kebab-case app name"

# Update path references (but be careful not to break functional paths)
replace_text "/Users/tars/clawd/projects/voice-app-downloadable" "/Users/tars/Desktop/waffler" "project path"
replace_text "voice-app-downloadable" "waffler" "project directory name"

# Update domain references
replace_text "voiceflow.app" "waffler.app" "domain name"
replace_text "api.voiceflow.app" "api.waffler.app" "API domain"

# Update bundle identifiers
replace_text "com.yourname.voiceflow" "com.yourname.waffler" "bundle identifier"
replace_text "ai.clawd.voiceflow" "ai.clawd.waffler" "bundle identifier"

# Update references to old pipeline name
replace_text "voice-agentic-pipeline" "waffler-pipeline" "old pipeline name"
replace_text "voice-prompt-tool" "waffler-prompt-tool" "old tool name"

echo "✅ Name cleanup complete!"
echo ""
echo "📝 Files that may still need manual review:"
echo "  - File names (LaunchWaffler.command, VoiceFlow.command, etc.)"
echo "  - Git history references"
echo "  - Hard-coded paths that are still valid"
echo ""