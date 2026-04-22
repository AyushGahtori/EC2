"""Write all tech-relevant O*NET roles to a readable file."""
from database import get_roles_collection
import re

col = get_roles_collection()
results = list(col.find({}, {'_id': 0, 'role': 1, 'code': 1}))
tech_keywords = re.compile(
    r'software|data|computer|network|web|security|database|machine|cloud|'
    r'analyst|developer|engineer|systems|information|scientist|intelligence|'
    r'statistician|mathematician|programmer|administrator|technician|architect',
    re.I
)
tech = [r for r in results if tech_keywords.search(r.get('role', ''))]

with open("tech_roles.txt", "w", encoding="utf-8") as f:
    for r in sorted(tech, key=lambda x: x['role']):
        f.write(f"{r['code']} | {r['role']}\n")

print(f"Written {len(tech)} tech roles to tech_roles.txt")
