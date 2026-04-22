"""Quick role matching test — writes results to file for inspection."""
import logging
logging.basicConfig(level=logging.INFO, format="%(levelname)s | %(message)s")

from database import get_roles_collection, ensure_indexes
from skill_gap import find_onet_role, compute_skill_gap

col = get_roles_collection()
ensure_indexes()

tests = [
    "Data Scientist",
    "Machine Learning Engineer",
    "Software Developer",
    "Data Analyst",
    "DevOps Engineer",
    "Backend Developer",
    "Information Security Analyst",
]

results = []
for query in tests:
    r = find_onet_role(query, col)
    matched = r.role if r else "NOT FOUND"
    results.append(f"  {query!r:40} -> {matched!r}")

output = "\n".join(results)
print("=== ROLE MATCHING RESULTS ===")
print(output)

# Full skill gap test
print("\n=== SKILL GAP TEST (Data Scientist) ===")
target = find_onet_role("Data Scientist", col)
if target:
    gap = compute_skill_gap(["python", "sql", "excel", "statistics"], target)
    print(f"  Matched O*NET role : {target.role}")
    print(f"  Coverage           : {gap.coverage_percent}%")
    print(f"  Matched skills     : {gap.matched_skills}")
    print(f"  Missing (top 5)    : {gap.missing_skills[:5]}")
else:
    print("  Role not found!")
