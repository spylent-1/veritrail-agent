"""Independent acceptance checks remain outside the model's workspace."""

import re
import shutil
import sys
from pathlib import Path

from repopilot.errors import RepoPilotError
from repopilot.models import JsonObject, RunReport
from repopilot.process import run_process
from repopilot.workspace import digest

PYTHON_CHECK = """import sys, types, pathlib
shop = types.ModuleType("shop_under_test")
path = pathlib.Path(sys.argv[1])
shop.__file__ = str(path)
exec(compile(path.read_bytes(), str(path), "exec"), shop.__dict__)
for price, discount, expected in [(0,0,0),(100,30,70),(20,30,0),(70,70,0)]:
    assert shop.discounted_price(price,discount) == expected
try: shop.discounted_price(-1,0)
except ValueError: pass
else: raise AssertionError("negative amount accepted")
for user, stock, expected in [(0,1,False),(-1,1,False),(1,0,False),(1,2,True)]:
    assert shop.can_claim(user,stock) is expected
for stock, quantity, expected in [(10,3,7),(10,10,0),(0,0,0)]:
    assert shop.remaining_stock(stock,quantity) == expected
for quantity in (-1,11):
    try: shop.remaining_stock(10,quantity)
    except ValueError: pass
    else: raise AssertionError("invalid quantity accepted")
print("HIDDEN_ACCEPTANCE_PASSED")
"""

JAVA_CHECK = """import demo.ShopService;
public class Acceptance {
  static void check(boolean value) { if (!value) throw new AssertionError("acceptance failed"); }
  public static void main(String[] args) {
    ShopService s = new ShopService();
    check(s.discountedPrice(100,30) == 70); check(s.discountedPrice(20,30) == 0);
    check(s.discountedPrice(70,70) == 0);
    try { s.discountedPrice(-1,0); throw new AssertionError(); }
    catch (IllegalArgumentException expected) {}
    check(!s.canClaim(0,1)); check(!s.canClaim(-1,1)); check(!s.canClaim(1,0));
    check(s.canClaim(1,2)); check(s.remainingStock(10,3) == 7);
    check(s.remainingStock(10,10) == 0); check(s.remainingStock(0,0) == 0);
    try { s.remainingStock(10,11); throw new AssertionError(); }
    catch (IllegalArgumentException expected) {}
    try { s.remainingStock(10,-1); throw new AssertionError(); }
    catch (IllegalArgumentException expected) {}
    System.out.println("HIDDEN_ACCEPTANCE_PASSED");
  }
}
"""


def grade_location(case: JsonObject, report: RunReport, root: Path) -> bool:
    path = root / case["file"]
    if report.status != "completed" or case["file"] not in report.answer:
        return False
    # Method name is a semantic anchor; no model-generated success flag is trusted.
    symbol = case["symbol"].split(".")[-1]
    if symbol not in report.answer or report.warnings:
        return False
    lines = [
        i + 1
        for i, text in enumerate(path.read_text(encoding="utf-8").splitlines())
        if case["symbol"] in text
    ]
    cited = set(re.findall(r"\[(E\d+)\]", report.answer))
    return any(
        ref.id in cited
        and ref.path == case["file"]
        and ref.sha256 == digest(path.read_bytes())
        and any(ref.start_line <= line <= ref.end_line for line in lines)
        for ref in report.evidence
    )


async def grade_edit(language: str, root: Path, private: Path) -> JsonObject:
    private.mkdir(parents=True, exist_ok=True)
    if language == "python":
        checker = private / "acceptance.py"
        checker.write_text(PYTHON_CHECK, encoding="utf-8")
        result = await run_process(
            [sys.executable, "-I", str(checker), str(root / "shop.py")], private, 20
        )
    else:
        javac, java = shutil.which("javac"), shutil.which("java")
        if not javac or not java:
            raise RepoPilotError(
                "JAVA_MISSING", "Java compiler is required for independent grading."
            )
        checker = private / "Acceptance.java"
        checker.write_text(JAVA_CHECK, encoding="utf-8")
        compiled = await run_process(
            [
                javac,
                "-encoding",
                "UTF-8",
                "-d",
                str(private),
                str(root / "src/main/java/demo/ShopService.java"),
                str(checker),
            ],
            private,
            30,
        )
        if compiled.returncode:
            return {"passed": False, "output": compiled.output, "stage": "compile"}
        result = await run_process([java, "-cp", str(private), "Acceptance"], private, 20)
    return {
        "passed": result.returncode == 0 and "HIDDEN_ACCEPTANCE_PASSED" in result.output,
        "output": result.output,
        "returncode": result.returncode,
    }
