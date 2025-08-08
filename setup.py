#!/usr/bin/env python3
"""
Job Informer Quick Start Script
Interactive setup for first-time users
"""

from pathlib import Path

def print_banner():
    """Print welcome banner"""
    print("🔍 Job Informer - Quick Start Setup")
    print("=" * 40)
    print()

def check_python_version():
    """Check Python version"""
    if sys.version_info < (3, 8):
        print("❌ Python 3.8+ is required")
        sys.exit(1)
    print(f"✅ Python {sys.version_info.major}.{sys.version_info.minor} detected")

def check_venv():
    """Check if virtual environment exists"""
    venv_path = Path(".venv")
    if venv_path.exists():
        print("✅ Virtual environment found")
        return True
    else:
        print("⚠️  Virtual environment not found")
        return False

def create_venv():
    """Create virtual environment"""
    print("Creating virtual environment...")
    try:
        subprocess.run([sys.executable, "-m", "venv", ".venv"], check=True)
        print("✅ Virtual environment created")
        return True
    except subprocess.CalledProcessError:
        print("❌ Failed to create virtual environment")
        return False

def install_dependencies():
    """Install dependencies"""
    print("Installing dependencies...")
    try:
        pip_path = ".venv/bin/pip" if os.name != 'nt' else ".venv\\Scripts\\pip.exe"
        subprocess.run([pip_path, "install", "-r", "requirements.txt"], check=True)
        print("✅ Dependencies installed")
        return True
    except subprocess.CalledProcessError:
        print("❌ Failed to install dependencies")
        return False

def setup_config():
    """Setup configuration file"""
    env_path = Path(".env")
    if env_path.exists():
        print("✅ Configuration file (.env) already exists")
        return True
    
    print("Setting up configuration...")
    
    # Copy example
    try:
        import shutil
        shutil.copy(".env.example", ".env")
        print("✅ Created .env file from template")
        print()
        print("📝 Next steps:")
        print("1. Edit .env file with your email credentials")
        print("2. Update job search keywords and locations")
        print("3. Run: python main.py --mode test-email")
        return True
    except Exception as e:
        print(f"❌ Failed to create .env file: {e}")
        return False

def run_test():
    """Run setup test"""
    print("\nRunning setup test...")
    try:
        python_path = ".venv/bin/python" if os.name != 'nt' else ".venv\\Scripts\\python.exe"
        result = subprocess.run([python_path, "test_setup.py"], 
                              capture_output=True, text=True, check=True)
        print("✅ Setup test passed")
        print(result.stdout)
        return True
    except subprocess.CalledProcessError as e:
        print("❌ Setup test failed")
        print(e.stdout)
        print(e.stderr)
        return False

def main():
    """Main setup function"""
    print_banner()
    
    # Check Python version
    check_python_version()
    
    # Check/create virtual environment
    if not check_venv():
        if not create_venv():
            sys.exit(1)
    
    # Install dependencies
    if not install_dependencies():
        sys.exit(1)
    
    # Setup configuration
    if not setup_config():
        sys.exit(1)
    
    # Run test
    run_test()
    
    print("\n🎉 Setup completed!")
    print("\n📋 Quick Commands:")
    print("• Test email:       python main.py --mode test-email")
    print("• Search once:      python main.py --mode run-once")
    print("• Quick test:       python main.py --mode quick-test")
    print("• Full summary:     python main.py --mode summary-full")
    print("• Latest summary:   python main.py --mode summary-latest")
    print("• Filter historical:python main.py --mode filter-historical")
    print("• Help:             python main.py --help")
    print("\n💡 Remember to edit .env with your actual credentials!")

if __name__ == "__main__":
    main()
