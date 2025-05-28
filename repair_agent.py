import os
import json
import subprocess
import sys
import re
from typing import List, Tuple, Dict
import google.generativeai as genai

class AutomatedCodeCorrector:
    def __init__(self, api_key: str):
        genai.configure(api_key=api_key)
        self.model = genai.GenerativeModel("gemini-1.5-flash")
        self.results = {}
        self.defect_patterns = {}
        
    def load_buggy_code(self, algo: str) -> str:
        """Load buggy code from python_programs directory"""
        try:
            with open(f"python_programs/{algo}.py", "r") as f:
                return f.read()
        except FileNotFoundError:
            print(f"Error: {algo}.py not found in python_programs/")
            return None
    
    def load_test_cases(self, algo: str) -> List[Tuple]:
        """Load test cases from json_testcases directory"""
        try:
            with open(f"json_testcases/{algo}.json", "r") as f:
                return [json.loads(line.strip()) for line in f.readlines()]
        except FileNotFoundError:
            print(f"Error: {algo}.json not found in json_testcases/")
            return []
    
    def load_correct_code(self, algo: str) -> str:
        """Load correct implementation for comparison"""
        try:
            with open(f"correct_python_programs/{algo}.py", "r") as f:
                return f.read()
        except FileNotFoundError:
            return None
    
    def generate_enhanced_prompt(self, algo: str, code: str, test_cases: List[Tuple]) -> str:
        """Generate a comprehensive prompt for code repair"""
        # Use multiple test cases for better context
        test_examples = "\n".join([
            f"assert {algo}({', '.join(map(str, tc[0] if isinstance(tc[0], list) else [tc[0]]))}) == {tc[1]}"
            for tc in test_cases[:3]  # Use first 3 test cases
        ])
        
        return f"""You are an expert Python debugger. Analyze the following buggy code and fix EXACTLY ONE line to make it work correctly.

IMPORTANT RULES:
1. Fix only ONE line - do not add, remove, or modify multiple lines
2. Preserve the original algorithm logic and structure
3. Common bug patterns: off-by-one errors, incorrect operators, wrong variable names, boundary conditions
4. Return ONLY the complete corrected function code

Algorithm: {algo}

Buggy Code:
```python
{code}
```

Test Cases (your fix must pass these):
{test_examples}

Analyze the bug and provide the corrected code:
"""
    
    def extract_code_from_response(self, response: str) -> str:
        """Extract Python code from Gemini's response"""
        # Try to find code blocks first
        code_block_pattern = r'```python\n(.*?)\n```'
        match = re.search(code_block_pattern, response, re.DOTALL)
        if match:
            return match.group(1).strip()
        
        # If no code block, try to find function definition
        func_pattern = r'def\s+\w+.*?(?=\n\n|\n(?=\w)|\Z)'
        match = re.search(func_pattern, response, re.DOTALL)
        if match:
            return match.group(0).strip()
        
        # Return the whole response if no pattern matches
        return response.strip()
    
    def validate_fix(self, algo: str, fixed_code: str, test_cases: List[Tuple]) -> Tuple[bool, int]:
        """Validate the fixed code against test cases"""
        # Save the fixed code temporarily
        temp_file = f"temp_{algo}.py"
        with open(temp_file, "w") as f:
            f.write(fixed_code)
        
        passed_tests = 0
        total_tests = len(test_cases)
        
        try:
            # Import the function dynamically
            spec = importlib.util.spec_from_file_location(algo, temp_file)
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
            func = getattr(module, algo)
            
            for test_input, expected_output in test_cases:
                try:
                    if isinstance(test_input, list):
                        result = func(*test_input)
                    else:
                        result = func(test_input)
                    
                    if result == expected_output:
                        passed_tests += 1
                except Exception as e:
                    print(f"Test failed with exception: {e}")
                    continue
                    
        except Exception as e:
            print(f"Error validating fix for {algo}: {e}")
        finally:
            # Clean up temp file
            if os.path.exists(temp_file):
                os.remove(temp_file)
        
        return passed_tests == total_tests, passed_tests
    
    def run_tester_py(self, algo: str) -> bool:
        """Run the built-in tester.py for validation"""
        try:
            result = subprocess.run([
                sys.executable, "tester.py", algo
            ], capture_output=True, text=True, timeout=30)
            
            return result.returncode == 0
        except subprocess.TimeoutExpired:
            print(f"Timeout running tester.py for {algo}")
            return False
        except Exception as e:
            print(f"Error running tester.py for {algo}: {e}")
            return False
    
    def analyze_defect_pattern(self, algo: str, buggy_code: str, fixed_code: str) -> str:
        """Analyze and categorize the type of defect"""
        # Simple pattern recognition - you can expand this
        if "range(" in buggy_code and "range(" in fixed_code:
            return "off-by-one-error"
        elif "==" in buggy_code and "!=" in fixed_code:
            return "incorrect-operator"
        elif ">" in buggy_code and "<" in fixed_code:
            return "boundary-condition"
        else:
            return "unknown-pattern"
    
    def repair_single_program(self, algo: str) -> Dict:
        """Repair a single program and return results"""
        print(f"\n🔧 Repairing {algo}...")
        
        # Load data
        buggy_code = self.load_buggy_code(algo)
        if not buggy_code:
            return {"success": False, "error": "Could not load buggy code"}
        
        test_cases = self.load_test_cases(algo)
        if not test_cases:
            return {"success": False, "error": "Could not load test cases"}
        
        # Generate repair
        prompt = self.generate_enhanced_prompt(algo, buggy_code, test_cases)
        response = self.model.generate_content(prompt)
        fixed_code = self.extract_code_from_response(response.text)
        
        # Validate fix
        validation_success, passed_tests = self.validate_fix(algo, fixed_code, test_cases)
        
        # Run official tester if available
        tester_success = self.run_tester_py(algo)
        
        # Save repaired code
        os.makedirs("repaired_programs", exist_ok=True)
        with open(f"repaired_programs/{algo}.py", "w") as f:
            f.write(fixed_code)
        
        # Analyze defect pattern
        defect_pattern = self.analyze_defect_pattern(algo, buggy_code, fixed_code)
        
        result = {
            "algorithm": algo,
            "success": validation_success and tester_success,
            "passed_tests": passed_tests,
            "total_tests": len(test_cases),
            "defect_pattern": defect_pattern,
            "fixed_code": fixed_code
        }
        
        print(f"✅ {algo}: {'PASSED' if result['success'] else 'FAILED'} ({passed_tests}/{len(test_cases)} tests)")
        return result
    
    def repair_all_programs(self) -> Dict:
        """Repair all programs in the dataset"""
        # Get all algorithm names from python_programs directory
        algorithms = []
        if os.path.exists("python_programs"):
            algorithms = [f.replace(".py", "") for f in os.listdir("python_programs") if f.endswith(".py")]
        
        results = {}
        successful_repairs = 0
        
        print(f"🚀 Starting automated repair for {len(algorithms)} programs...")
        
        for algo in algorithms:
            try:
                result = self.repair_single_program(algo)
                results[algo] = result
                if result.get("success", False):
                    successful_repairs += 1
            except Exception as e:
                print(f"❌ Error repairing {algo}: {e}")
                results[algo] = {"success": False, "error": str(e)}
        
        # Calculate overall statistics
        success_rate = (successful_repairs / len(algorithms)) * 100 if algorithms else 0
        print(f"\n📊 Overall Results: {successful_repairs}/{len(algorithms)} successful repairs ({success_rate:.1f}%)")
        
        # Save detailed results
        with open("repair_results.json", "w") as f:
            json.dump(results, f, indent=2)
        
        return results
    
    def generate_report(self, results: Dict):
        """Generate a comprehensive evaluation report"""
        report = []
        report.append("# Automated Code Correction Report\n")
        
        # Summary statistics
        total_programs = len(results)
        successful_repairs = sum(1 for r in results.values() if r.get("success", False))
        success_rate = (successful_repairs / total_programs) * 100 if total_programs > 0 else 0
        
        report.append(f"## Summary")
        report.append(f"- Total Programs: {total_programs}")
        report.append(f"- Successful Repairs: {successful_repairs}")
        report.append(f"- Success Rate: {success_rate:.1f}%\n")
        
        # Defect pattern analysis
        defect_counts = {}
        for result in results.values():
            pattern = result.get("defect_pattern", "unknown")
            defect_counts[pattern] = defect_counts.get(pattern, 0) + 1
        
        report.append("## Defect Pattern Analysis")
        for pattern, count in defect_counts.items():
            report.append(f"- {pattern}: {count} occurrences")
        report.append("")
        
        # Detailed results
        report.append("## Detailed Results")
        for algo, result in results.items():
            status = "✅ PASSED" if result.get("success", False) else "❌ FAILED"
            tests_info = f"{result.get('passed_tests', 0)}/{result.get('total_tests', 0)} tests"
            report.append(f"- {algo}: {status} ({tests_info})")
        
        # Save report
        with open("evaluation_report.md", "w") as f:
            f.write("\n".join(report))
        
        print("📄 Report saved to evaluation_report.md")

def main():
    # Load environment variables
    def load_env_file():
        env_path = '.env'
        if os.path.exists(env_path):
            with open(env_path, 'r') as f:
                for line in f:
                    line = line.strip()
                    if line and not line.startswith('#') and '=' in line:
                        key, value = line.split('=', 1)
                        os.environ[key.strip()] = value.strip()
    
    load_env_file()
    api_key = os.getenv("GEMINI_API_KEY")
    
    if not api_key:
        print("Error: GEMINI_API_KEY not found in environment variables.")
        print("Please create a .env file with: GEMINI_API_KEY=your_api_key_here")
        return
    
    corrector = AutomatedCodeCorrector(api_key)
    
    # Interactive mode
    while True:
        print("\n🔧 Automated Code Correction System")
        print("1. Repair single program")
        print("2. Repair all programs")
        print("3. Exit")
        
        choice = input("Choose an option (1-3): ").strip()
        
        if choice == "1":
            algo = input("Enter algorithm name: ").strip()
            result = corrector.repair_single_program(algo)
            print(f"Result: {result}")
            
        elif choice == "2":
            results = corrector.repair_all_programs()
            corrector.generate_report(results)
            
        elif choice == "3":
            print("Goodbye!")
            break
        else:
            print("Invalid choice. Please try again.")

if __name__ == "__main__":
    # Add missing import
    import importlib.util
    main()