import json
import matplotlib.pyplot as plt
from collections import Counter, defaultdict
import pandas as pd

# Helper: safely check if key exists and is non-empty
def has_attr(entry, attr):
    return attr in entry and entry[attr] not in (None, "Unknown", "")

with open("dependency_dataset.json", "r", encoding="utf-8") as f:
    data = json.load(f)

# Results containers
direct_counts = {}  # dependency -> number of direct deps
direct_frequency = Counter()
repo_dependency_counts = Counter()
repo_attribute_counts = defaultdict(lambda: Counter())

for dep in data:
    dep_id = dep["_id"]
    
    # Count direct dependencies
    direct = dep.get("direct_dependencies", [])
    direct_counts[dep_id] = len(direct)
    
    # Update frequency counts for each direct dependency group id
    for t in direct:
        parts = t.split(":")
        direct_frequency[parts[0]] += 1

    # Attribute coverage
    for attr in ["description", "source_code_url", "last_modified", "jar_size", "parent_module"]:
        if has_attr(dep, attr):
            for repo in dep["origin_repository"]:
                repo_attribute_counts[repo][attr] += 1

    # Count dependencies per repository
    for repo in dep["origin_repository"]:
        repo_dependency_counts[repo] += 1

# ---- Results ----
# Total number of dependencies
print("Number of dependencies in dataset:", len(data))

# Top 10 most frequent direct dependencies
print("Top 10 most frequent direct dependencies groups:")
for dep, freq in direct_frequency.most_common(10):
    print(dep, "->", freq)

# Data: number of direct dependencies per dependency
num_direct_deps = list(direct_counts.values())

# Shift 0-values slightly above 0 for log scale
data_for_plot = [x+0.1 for x in num_direct_deps]

fig, ax = plt.subplots(figsize=(10, 4))

# Horizontal boxplot
ax.boxplot(data_for_plot, patch_artist=True, vert=False,
           boxprops=dict(facecolor='#1f4e79', color='k', linewidth=1.5),
           whiskerprops=dict(color='k', linewidth=1.2),
           capprops=dict(color='k', linewidth=1.2),
           medianprops=dict(color='#ff7f0e', linewidth=2),
           flierprops=dict(marker='o', markerfacecolor='#aec7e8', markeredgecolor='k', markersize=4, alpha=0.9))

# Log scale
ax.set_xscale('log')

# Regular ticks
xticks = [0.1, 1, 2, 5, 10, 20, 50, 100]
ax.set_xticks(xticks)
ax.set_xticklabels(['0', '1', '2', '5', '10', '20', '50', '100'])
ax.set_xlabel("Number of Direct Dependencies", fontsize=12)

# Remove y-axis label
ax.get_yaxis().set_visible(False)

# Title
ax.set_title("Distribution of Direct Dependencies per Dependency", fontsize=14, fontweight='bold')

# Grid
ax.grid(axis='x', linestyle='--', alpha=0.7)

plt.tight_layout()
plt.show()

# ---- Attribute Coverage per Repository ----
# Convert to DataFrame for plotting percentages
df = pd.DataFrame(repo_attribute_counts)
df = df.divide(pd.Series(repo_dependency_counts), axis=1) * 100  # percent

# Optional: add total artifacts as reference
df.loc["Total Artifacts"] = 100

# Gradient palette: 4 shades of gray → 4 shades of blue
gradient_colors = ['#d9d9d9', '#b2b2b2', '#7f7f7f', '#4d4d4d',  # gray (light→dark)
                   '#4292c6', '#2171b5', '#08519c', '#08306b']  # blue (light→dark)

colors = gradient_colors[:len(df.index)]

# Plot
fig, ax = plt.subplots(figsize=(10, 6))
df.T.plot(kind='barh', ax=ax, color=colors, edgecolor='k', alpha=0.9, stacked=False)

# Labels and title
ax.set_xlabel("Percentage (%)", fontsize=12)
ax.set_ylabel("Repository", fontsize=12)
ax.set_title("Attribute Coverage per Repository", fontsize=14, fontweight='bold')
ax.set_xlim(0, 105)

# Legend formatting
ax.legend(title="Attributes", bbox_to_anchor=(1.05, 1), loc='upper left', fontsize=10)

plt.tight_layout()
plt.show()