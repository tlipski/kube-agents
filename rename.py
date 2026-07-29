import os
import glob

def replace_in_file(filepath, replacements):
    with open(filepath, 'r') as f:
        content = f.read()
    
    new_content = content
    for old, new in replacements:
        new_content = new_content.replace(old, new)
        
    if new_content != content:
        with open(filepath, 'w') as f:
            f.write(new_content)
        print(f"Updated {filepath}")

replacements = [
    ("AgentExtension", "AgentPlugin"),
    ("agentextension", "agentplugin"),
    ("AgentExtensions", "AgentPlugins"),
    ("agentextensions", "agentplugins"),
    ("Agent extension", "Agent plugin"),
    ("Agent Extension", "Agent Plugin"),
]

for root, dirs, files in os.walk('/usr/local/google/home/tomeklipski/d/ka-dev/'):
    for file in files:
        if file.endswith('.go') or file.endswith('.yaml') or file.endswith('.sh') or file.endswith('.md'):
            # Skip the script itself and some dirs
            if '.git' in root or '.bin' in root:
                continue
            filepath = os.path.join(root, file)
            replace_in_file(filepath, replacements)

# rename files
for root, dirs, files in os.walk('/usr/local/google/home/tomeklipski/d/ka-dev/'):
    for file in files:
        if "agentextension" in file:
            old_path = os.path.join(root, file)
            new_path = os.path.join(root, file.replace("agentextension", "agentplugin"))
            os.rename(old_path, new_path)
            print(f"Renamed {old_path} to {new_path}")

print("Done")
