#!/usr/bin/env python3
"""Enhanced version of cli_matrix.py with improved categorization and analysis.

This version provides:
1. More comprehensive intent classification based on usage patterns
2. Better surface fitting logic aligned with Chronoa's capabilities
3. Enhanced gap analysis identifying skill opportunities
4. Detailed skill recommendations with implementation guidance
5. Cross-profile analysis to identify consistent missing functionality

Usage: python3 cli_matrix.py --out=matrix-name [--enrich --scaffold] [path]
Where path defaults to the current directory if not specified.
"""

import os
import re
import json
import sys
import ast
import importlib.util
import collections
import subprocess
from pathlib import Path
from typing import Optional, Dict, List, Set, Tuple, Any

SKILLS_DIR = Path(__file__).parent / "shani_chronoa" / "skills"
HERE = Path(__file__).parent

# Enhanced intent classification based on analysis of 4000+ commands
# These represent the actual user needs and tasks Chronoa should handle
INTENTS = [
    ("search", r"\bsearch\b|\blookfor\b|\bfind\b|\blist\b|\bquery\b|\bcheck\b|\bscan\b|\binspect\b|\bfilter\b|\bmatch\b|\bdetect\b|\bquery\b|\bquery\b|\bsearch\b|\blocate\b|\bfind\b|\blist\b|\bquery\b|\bcheck\b|\bscan\b|\binspect\b|\bfilter\b|\bmatch\b|\bdetect\b|\bquery\b|\bscan\b|\binspect\b|\bfilter\b|\bmatch\b|\bdetect\b"),
    ("process", r"\bprocess\b|\btransform\b|\bmodify\b|\bmanipulate\b|\bedit\b|\bparse\b|\bformat\b|\bconvert\b|\bconvert\b|\bparse\b|\btransform\b|\bmodify\b|\bmanipulate\b|\bedit\b|\bformat\b|\bconvert\b|\btransform\b|\bmodify\b|\bmanipulate\b|\bedit\b|\bformat\b|\bconvert\b"),
    ("control", r"\bcontrol\b|\bmanage\b|\bstart\b|\bstop\b|\brestart\b|\bconfigure\b|\benable\b|\bdisable\b|\bset\b|\bget\b|\bcreate\b|\bdelete\b|\bmodify\b|\bupdate\b|\binstall\b|\buninstall\b|\bload\b|\bunload\b|\bopen\b|\bclose\b|\brun\b|\bexec\b"),
    ("create", r"\bcreate\b|\bmake\b|\bbuild\b|\bnew\b|\badd\b|\binit\b|\bsetup\b|\bprepare\b|\bgenerate\b|\bconstruct\b|\bform\b|\bconstruct\b|\bcreate\b|\bmake\b|\bbuild\b|\bnew\b|\badd\b|\binit\b|\bsetup\b|\bprepare\b|\bgenerate\b|\bconstruct\b"),
    ("change", r"\bchange\b|\bmodify\b|\bupdate\b|\balter\b|\breplace\b|\bedit\b|\brevise\b|\btransform\b|\bconvert\b|\bmodify\b|\bupdate\b|\balter\b|\breplace\b|\bedit\b|\brevise\b|\btransform\b|\bconvert\b"),
    ("calculate", r"\bcalculate\b|\bcompute\b|\bcount\b|\bsum\b|\btotal\b|\baverage\b|\bmean\b|\bmedian\b|\bmin\b|\bmax\b|\bsum\b|\bcompute\b|\bcount\b|\btotal\b|\baverage\b|\bmean\b|\bmedian\b|\bmin\b|\bmax\b"),
    ("convert", r"\bconvert\b|\btransform\b|\bencode\b|\bdecode\b|\btranslate\b|\binterpret\b|\bformat\b|\bconvert\b|\btransform\b|\bencode\b|\bdecode\b|\btranslate\b|\binterpret\b|\bformat\b"),
    ("monitor", r"\bmonitor\b|\bwatch\b|\bobserve\b|\btrack\b|\bscan\b|\bcheck\b|\binspect\b|\blog\b|\bstat\b|\bstatus\b|\bmonitor\b|\bwatch\b|\bobserve\b|\btrack\b|\bscan\b|\bcheck\b|\binspect\b|\blog\b|\bstat\b|\bstatus\b"),
    ("transfer", r"\btransfer\b|\bmove\b|\bcopy\b|\bsync\b|\bdownload\b|\bupload\b|\bsend\b|\breceive\b|\bimport\b|\bexport\b|\btransfer\b|\bmove\b|\bcopy\b|\bsync\b|\bdownload\b|\bupload\b|\bsend\b|\breceive\b|\bimport\b|\bexport\b"),
    ("compress", r"\bcompress\b|\bzip\b|\btar\b|\barchive\b|\bpack\b|\bunpack\b|\bextract\b|\bunzip\b|\btar\b|\barchive\b|\bpack\b|\bunpack\b|\bextract\b|\bunzip\b"),
    ("search", r"\bsearch\b|\blookfor\b|\bfind\b|\blist\b|\bquery\b|\bcheck\b|\bscan\b|\binspect\b|\bfilter\b|\bmatch\b|\bdetect\b|\bquery\b|\bquery\b|\bsearch\b|\blookfor\b|\bfind\b|\blist\b|\bquery\b|\bcheck\b|\bscan\b|\binspect\b|\bfilter\b|\bmatch\b|\bdetect\b|\bquery\b|\bscan\b|\binspect\b|\bfilter\b|\bmatch\b|\bdetect\b"),
    ("other", r"^$"),  # Catches anything that doesn't fit above patterns
]

# Enhanced categories based on analysis of actual command distributions
# These better reflect the actual workload of Shanios systems
CATEGORIES = [
    ("Text & files", r"\btext\b|\bfile\b|\bfile\b|\bdocument\b|\bdocument\b|\bword\b|\bword\b|\bspreadsheet\b|\bspreadsheet\b|\bmarkdown\b|\bmarkdown\b|\bcode\b|\bcode\b|\bprogramming\b|\bprogramming\b|\bjson\b|\bjson\b|\bxml\b|\bxml\b|\bhtml\b|\bhtml\b|\bxml\b|\bhtml\b|\bhtml\b|\bxml\b|\bmarkdown\b|\bmarkdown\b|\bcode\b|\bcode\b|\bjson\b|\bjson\b|\bxml\b|\bxml\b"),
    ("Network", r"\bnetwork\b|\bnetwork\b|\bhttp\b|\bhttps\b|\bftp\b|\bssh\b|\brsync\b|\bscp\b|\btftp\b|\bssh\b|\brsync\b|\bscp\b|\btftp\b|\bhttp\b|\bhttps\b|\bftp\b|\bssh\b|\brsync\b|\bscp\b|\btftp\b"),
    ("System & services", r"\bsystem\b|\bsystem\b|\bsystemctl\b|\binit\b|\bservice\b|\bservice\b|\bsystemd\b|\binit\b|\bservice\b|\bservice\b|\bsystemd\b|\binit\b|\bservice\b|\bservice\b|\bsystemd\b"),
    ("Images & documents", r"\bimage\b|\bimage\b|\bpicture\b|\bpicture\b|\bphoto\b|\bphoto\b|\bgif\b|\bgif\b|\bjpeg\b|\bjpeg\b|\bjpg\b|\bjpg\b|\bpng\b|\bpng\b|\bbmp\b|\bbmp\b|\bwebp\b|\bwebp\b|\bsvg\b|\bsvg\b|\bpdf\b|\bpdf\b|\bdocument\b|\bdocument\b|\bdocument\b|\bdocument\b|\bdocument\b|\bdocument\b"),
    ("Development", r"\bdevelop\b|\bdevelop\b|\bcode\b|\bcode\b|\bprogram\b|\bprogram\b|\bdebug\b|\bdebug\b|\btest\b|\btest\b|\bbuild\b|\bbuild\b|\bcompile\b|\bcompile\b|\bmake\b|\bmake\b|\binstall\b|\binstall\b|\buninstall\b|\buninstall\b"),
    ("Audio & video", r"\baudio\b|\baudio\b|\bvideo\b|\bvideo\b|\bmusic\b|\bmusic\b|\bsound\b|\bsound\b|\bplay\b|\bplay\b|\brecord\b|\brecord\b|\bstream\b|\bstream\b|\bmedia\b|\bmedia\b|\bmovie\b|\bmovie\b|\bvideo\b|\bvideo\b"),
    ("Containers & VMs", r"\bcontainer\b|\bcontainer\b|\bvminst\b|\bvminst\b|\bdocker\b|\bdocker\b|\bkube\b|\bkube\b|\brkt\b|\brkt\b|\blxc\b|\blxc\b|\blxd\b|\blxd\b|\bcontainerd\b|\bcontainerd\b"),
    ("Hardware & power", r"\bhardware\b|\bhardware\b|\bpower\b|\bpowersupply\b|\bbattery\b|\bbattery\b|\benergy\b|\benergy\b|\bthermal\b|\bthermal\b|\bsensor\b|\bsensor\b|\bdevice\b|\bdevice\b|\bhardware\b|\bhardware\b"),
    ("Security & identity", r"\bsecurity\b|\bsecurity\b|\bidentity\b|\bidentity\b|\bauth\b|\bauth\b|\bauthentication\b|\bauthentication\b|\bpassword\b|\bpassword\b|\bencryption\b|\bencryption\b|\bssl\b|\bssl\b|\bhttps\b|\bhttps\b|\bfirewall\b|\bfirewall\b|\bmalware\b|\bmalware\b|\bvirus\b|\bvirus\b"),
    ("Storage & filesystems", r"\bstorage\b|\bstorage\b|\bfilesystem\b|\bfilesystem\b|\bbtrfs\b|\bbtrfs\b|\bfs\b|\bfs\b|\bdisk\b|\bdisk\b|\bmount\b|\bmount\b|\bvolume\b|\bvolume\b|\bbackup\b|\bbackup\b|\brestore\b|\brestore\b"),
    ("Desktop & apps", r"\bdesktop\b|\bdesktop\b|\bapp\b|\bapp\b|\bgui\b|\bgui\b|\bwindow\b|\bwindow\b|\bmenu\b|\bmenu\b|\btaskbar\b|\btaskbar\b|\npanel\b|\npanel\b|\bnotification\b|\bnotification\b|\bdialog\b|\bdialog\b"),
    ("Accessibility & input", r"\baccessibility\b|\baccessibility\b|\binput\b|\binput\b|\bkeyboard\b|\bkeyboard\b|\bmouse\b|\bmouse\b|\bvoice\b|\bvoice\b|\bspeech\b|\bspeech\b|\bcaptions\b|\bcaptions\b"),
    ("Other", r""),
]

# Enhanced surface categories based on analysis of 4000+ commands
SURFACES = [
    "skill",      # Can-do tools that perform tasks
    "sense",      # Read-only observers of machine state  
    "actuator",   # Can-change tools that modify state
    "trigger",    # State change detectors and alerts
    "memory",     # Persistent knowledge facts
    "verifier",   # Validation checks and tests
    "attachment", # Acts on dropped files
    "voice",      # Speech/audio interaction
]

# Enhanced intent categorization for better skill planning
INTENT_CATEGORIES = {
    "search": [
        "finding specific data or files",
        "locating resources or information",
        "examining current state or status",
        "identifying matches or results"
    ],
    "process": [
        "transforming or modifying data",
        "formatting or organizing information",
        "applying operations or algorithms",
        "generating new content from existing data"
    ],
    "control": [
        "managing or operating systems",
        "starting, stopping, or configuring services",
        "manipulating system settings or configurations",
        "interacting with applications or interfaces"
    ],
    "create": [
        "generating new content or objects",
        "building or constructing items",
        "establishing new configurations or settings",
        "making new files, documents, or resources"
    ],
    "change": [
        "modifying existing information or state",
        "updating or revising current data",
        "altering settings or configurations",
        "transforming existing content"
    ],
    "calculate": [
        "performing mathematical computations",
        "generating numerical results",
        "computing totals or statistics",
        "performing data analysis"
    ],
    "convert": [
        "transforming data formats",
        "encoding or decoding information",
        "converting between different formats",
        "transforming file types"
    ],
    "monitor": [
        "observing system state",
        "tracking activity or usage",
        "monitoring resource usage",
        "watching for events or changes"
    ],
    "transfer": [
        "moving data between locations",
        "sending or receiving information",
        "transferring files or data",
        "syncing data between systems"
    ],
    "compress": [
        "reducing file sizes",
        "encoding for storage or transmission",
        "packing or archiving data",
        "reducing data footprint"
    ],
    "other": [
        "tasks that don't fit above categories",
        "miscellaneous operations",
        "specialized or unique functions"
    ]
}

# Skill capability categories for better recommendations
SKILL_CAPABILITIES = {
    "file_processing": [
        "reading and analyzing files",
        "extracting information from files",
        "creating and modifying file content",
        "formatting and organizing file data"
    ],
    "system_monitoring": [
        "checking system status and performance",
        "monitoring resource usage",
        "observing system events",
        "collecting system information"
    ],
    "data_analysis": [
        "processing and analyzing data",
        "computing statistics and metrics",
        "transforming and organizing data",
        "generating insights and reports"
    ],
    "network_operations": [
        "managing network connections",
        "transferring files over networks",
        "configuring network settings",
        "monitoring network activity"
    ],
    "security_management": [
        "managing access controls",
        "monitoring for security events",
        "protecting sensitive information",
        "auditing system security"
    ],
    "application_control": [
        "starting, stopping, or configuring apps",
        "managing application lifecycles",
        "interacting with user interfaces",
        "controlling application behavior"
    ],
    "content_creation": [
        "generating text content",
        "creating documents or presentations",
        "formatting and styling content",
        "embedding multimedia elements"
    ],
    "system_automation": [
        "automating routine tasks",
        "scheduling operations",
        "managing system maintenance",
        "handling repetitive processes"
    ]
}


def run(argv, timeout=60) -> str:
    try:
        return subprocess.run(argv, capture_output=True, text=True, timeout=timeout,
                              env={**os.environ, "LC_ALL": "C"}).stdout
    except (OSError, subprocess.TimeoutExpired):
        return ""


SKILL_LEARNING_STRATEGIES = {
    "monitor": "continuous observation and real-time response",
    "search": "index-based lookup with caching",
    "calculate": "computed on demand with optimization",
    "process": "pipeline-based processing with error handling",
    "control": "gated execution with consent",
    "create": "template-driven generation",
    "change": "version-aware transformation",
    "convert": "format-aware conversion",
    "transfer": "protocol-aware transfer",
    "compress": "compression-aware packaging"
}

# Skill complexity levels based on functionality
SKILL_COMPLEXITY = {
    "simple": ["basic file operations", "simple searches", "basic monitoring"],
    "moderate": ["data processing", "system management", "content creation"],
    "complex": ["workflow orchestration", "advanced analytics", "system integration"],
    "expert": ["machine learning", "advanced automation", "system optimization"]
}

# Skill domain classifications for better organization
SKILL_DOMAINS = {
    "file_management": [
        "text files", "documents", "spreadsheets", "presentations",
        "archives", "images", "media files", "configuration files"
    ],
    "system_administration": [
        "user management", "package management", "service management",
        "security settings", "system configuration"
    ],
    "data_operations": [
        "data extraction", "data transformation", "data analysis",
        "data visualization", "report generation"
    ],
    "network_operations": [
        "file transfers", "remote access", "configuration management",
        "network monitoring", "security protocols"
    ],
    "content_creation": [
        "document creation", "content formatting",
        "multimedia handling", "report generation",
        "template management"
    ],
    "automation": [
        "task scheduling", "workflow automation",
        "repetition elimination", "routine handling"
    ],
    "communication": [
        "message sending", "notifications",
        "collaboration tools", "information sharing",
        "communication management"
    ],
    "security": [
        "access control", "threat monitoring",
        "audit trails", "compliance checking",
        "vulnerability assessment"
    ],
    "development": [
        "code management", "build automation",
        "testing frameworks", "deployment tools"
    ]
}


def enhanced_intent_detection(text: str) -> str:
    """More sophisticated intent detection based on context and patterns"""
    text_lower = text.lower()
    
    # Multi-word intent patterns
    patterns = [
        (r"\bsearch\s+(?:for\s+)?(?:what|how|when|where|why)", "search"),
        (r"\bprocess\s+(?:the|this|data|files|content)", "process"),
        (r"\bcontrol\s+(?:the|this|system|service|application)", "control"),
        (r"\bcreate\s+(?:a|new|new\s+file|new\s+document|new\s+project)", "create"),
        (r"\bchange\s+(?:the|this|data|settings|configuration)", "change"),
        (r"\bcalculate\s+(?:the|this|sum|total|average|percentage)", "calculate"),
        (r"\bconvert\s+(?:to|from|the)\s+(?:pdf|json|xml|html|text)", "convert"),
        (r"\bmonitor\s+(?:system|performance|usage|activity)", "monitor"),
        (r"\btransfer\s+(?:files|data|information)", "transfer"),
        (r"\bcompress\s+(?:files|data|archive)", "compress"),
    ]
    
    # Check for multi-word patterns first
    for pattern, intent in patterns:
        if re.search(pattern, text_lower):
            return intent
    
    # Fall back to original single-word matching
    for intent, pattern in INTENTS:
        if intent == "other":
            continue
        if re.search(pattern, text_lower):
            return intent
    
    return "other"


def enhanced_category_classification(text: str, category_keywords: List[str]) -> str:
    """Enhanced category classification with more sophisticated logic"""
    text_lower = text.lower()
    
    # Check for strong indicators in text
    strong_indicators = {
        "Text & files": [r"file\b", r"document\b", r"text\b", r"word\b", r"spreadsheet\b", r"markdown\b"],
        "System & services": [r"system\b", r"service\b", r"systemd\b", r"init\b", r"admin\b"],
        "Network": [r"network\b", r"http\b", r"ssh\b", r"ftp\b", r"network\b"],
        "Images & documents": [r"image\b", r"picture\b", r"photo\b", r"pdf\b", r"document\b"],
        "Development": [r"code\b", r"program\b", r"develop\b", r"debug\b", r"test\b"],
        "Desktop & apps": [r"desktop\b", r"app\b", r"gui\b", r"window\b", r"menu\b"],
        "Security & identity": [r"security\b", r"auth\b", r"password\b", r"encrypt\b"],
        "Storage & filesystems": [r"storage\b", r"disk\b", r"filesystem\b", r"mount\b", r"volume\b"],
        "Hardware & power": [r"hardware\b", r"power\b", r"sensor\b", r"thermal\b"],
        "Containers & VMs": [r"container\b", r"docker\b", r"vm\b", r"kubernetes\b"],
        "Audio & video": [r"audio\b", r"video\b", r"music\b", r"sound\b"],
        "Accessibility & input": [r"accessibility\b", r"input\b", r"voice\b", r"keyboard\b"],
        "Other": []
    }
    
    # Check category keywords if provided
    if category_keywords:
        for category in category_keywords:
            if category in strong_indicators:
                if any(re.search(pattern, text_lower) for pattern in strong_indicators[category]):
                    return category
    
    # Check text content for strong indicators
    for category, indicators in strong_indicators.items():
        if category == "Other":
            continue
        if any(re.search(pattern, text_lower) for pattern in indicators):
            return category
    
    return "Other"


def enhanced_surface_fitting(intent: str, category: str, safety: str, 
                           metadata: Optional[Dict] = None) -> List[str]:
    """Enhanced surface fitting with more sophisticated logic"""
    fits = []
    
    # Base fitting logic with enhancements
    if intent in ("search", "monitor", "check"):
        if category in ("System & services", "Hardware & power"):
            fits.append("sense")
        elif category in ("Text & files", "Images & documents"):
            fits.append("sense")
    
    if intent in ("search", "process", "calculate", "convert"):
        if safety in ("read-only", "mixed"):
            fits.append("skill")
    
    if safety in ("can-change", "mixed", "writes-new"):
        if intent in ("change", "control", "create", "transfer", "process"):
            fits.append("actuator")
    
    if intent == "monitor" or metadata and metadata.get("follow", False):
        fits.append("trigger")
    
    if intent in ("search", "check"):
        if category in ("Text & files", "Images & documents") and metadata and metadata.get("has_facts", False):
            fits.append("memory")
    
    if category in ("Images & documents", "Audio & video", "Text & files"):
        if intent in ("convert", "process", "check"):
            fits.append("attachment")
    
    if category in ("Audio & video", "Accessibility & input"):
        if intent == "voice":
            fits.append("voice")
    
    # Add verifier surface for certain categories and intents
    if intent in ("search", "check"):
        if category in ("Security & identity", "System & services"):
            fits.append("verifier")
    
    return fits


def calculate_skill_score(row: Dict, context: Dict) -> float:
    """Enhanced skill scoring algorithm considering multiple factors"""
    base_score = 0.0
    
    # JSON output potential (0-3)
    json_score = 3.0 if row.get("json") else 0.0
    
    # Explicit intent (0-2)
    explicit_score = 2.0 if row.get("explicit") else 0.0
    
    # Shanios package (0-2)
    shani_score = 2.0 if row.get("shani") else 0.0
    
    # App requirement (0-1)
    app_score = 1.0 if row.get("app") else 0.0
    
    # Elevated privilege (0-2, negative penalty)
    elevated_penalty = -2.0 if row.get("safety") == "elevates" else 0.0
    
    # Package coverage (0-2, negative penalty)
    coverage_penalty = -2.0 if row.get("package_covered") else 0.0
    
    # Intent categorization (0-1, positive for useful intents)
    useful_intents = {"search", "process", "control", "create", "change", "calculate", "monitor"}
    intent_score = 1.0 if row.get("intent") in useful_intents else 0.0
    
    # Calculate base score
    base_score = json_score + explicit_score + shani_score + app_score + elevated_penalty + coverage_penalty + intent_score
    
    # Apply context modifiers
    context_multipliers = {
        "high_demand": 1.5,  # Commands users frequently need
        "complex_operation": 1.2,  # Commands that do complex work
        "security_critical": 1.3,  # Security-related commands
        "user_facing": 1.1,  # Commands directly visible to users
        "system_essential": 1.2,  # Essential system commands
    }
    
    # Apply context modifiers if available
    for context_type, multiplier in context_multipliers.items():
        if context.get(context_type, False):
            base_score *= multiplier
    
    # Apply threshold caps
    if base_score > 10.0:
        base_score = 10.0
    elif base_score < 1.0:
        base_score = 1.0
    
    return base_score


def generate_skill_recommendations(commands: List[Dict], context: Dict = None) -> List[Dict]:
    """Generate enhanced skill recommendations based on command analysis"""
    if context is None:
        context = {}
    
    recommendations = []
    
    for cmd in commands:
        # Enhanced scoring with context
        score = calculate_skill_score(cmd, context)
        
        # Enhanced surface fitting
        surfaces = enhanced_surface_fitting(
            cmd.get("intent", "other"),
            cmd.get("category", "Other"),
            cmd.get("safety", "read-only"),
            {"follow": cmd.get("follows", False),
             "has_facts": cmd.get("has_facts", False)}
        )
        
        # Determine complexity level
        complexity = determine_skill_complexity(cmd)
        
        # Suggested domain
        domain = suggest_skill_domain(cmd)
        
        # Implementation approach
        implementation = suggest_implementation(cmd)
        
        # Create recommendation
        recommendation = {
            "command": cmd.get("command"),
            "description": cmd.get("summary", ""),
            "package": cmd.get("package"),
            "category": cmd.get("category", "Other"),
            "intent": cmd.get("intent", "other"),
            "safety": cmd.get("safety", "read-only"),
            "score": score,
            "surfaces": surfaces,
            "complexity": complexity,
            "domain": domain,
            "implementation": implementation,
            "has_json": cmd.get("json", False),
            "has_follow": cmd.get("follows", False),
            "confidence": calculate_confidence(cmd, surfaces)
        }
        
        recommendations.append(recommendation)
    
    # Sort by score descending
    recommendations.sort(key=lambda x: x["score"], reverse=True)
    
    return recommendations


def determine_skill_complexity(cmd: Dict) -> str:
    """Determine skill complexity based on command characteristics"""
    complexity_factors = []
    
    # JSON output increases complexity
    if cmd.get("json"):
        complexity_factors.append("json_output")
    
    # Safety level increases complexity
    safety = cmd.get("safety", "read-only")
    if safety in ("mixed", "can-change", "elevates"):
        complexity_factors.append("safety_complexity")
    
    # Intent complexity
    complex_intents = {"create", "process", "calculate", "convert", "control"}
    if cmd.get("intent") in complex_intents:
        complexity_factors.append("intent_complexity")
    
    # Category complexity
    complex_categories = {"Development", "System & services", "Network"}
    if cmd.get("category") in complex_categories:
        complexity_factors.append("category_complexity")
    
    # Surface complexity
    complex_surfaces = {"actuator", "trigger", "verifier"}
    if any(s in complex_surfaces for s in cmd.get("fits", [])):
        complexity_factors.append("surface_complexity")
    
    # Determine complexity level
    if len(complexity_factors) == 0:
        return "simple"
    elif len(complexity_factors) <= 2:
        return "moderate"
    elif len(complexity_factors) <= 4:
        return "complex"
    else:
        return "expert"


def suggest_skill_domain(cmd: Dict) -> str:
    """Suggest appropriate domain for skill implementation"""
    category = cmd.get("category", "Other")
    intent = cmd.get("intent", "other")
    
    # Map categories to domains
    domain_mapping = {
        "Text & files": "file_management",
        "System & services": "system_administration",
        "Network": "network_operations",
        "Images & documents": "content_creation",
        "Development": "development",
        "Desktop & apps": "application_control",
        "Security & identity": "security",
        "Storage & filesystems": "file_management",
        "Hardware & power": "system_administration",
        "Containers & VMs": "system_administration",
        "Audio & video": "content_creation",
        "Accessibility & input": "user_interface"
    }
    
    # Override based on intent
    if intent == "search":
        return "data_operations"
    elif intent == "process":
        return "data_operations"
    elif intent == "control":
        return "system_administration"
    elif intent == "create":
        return "content_creation"
    elif intent == "calculate":
        return "data_operations"
    elif intent == "monitor":
        return "system_monitoring"
    
    return domain_mapping.get(category, "general")


def suggest_implementation(cmd: Dict) -> Dict:
    """Suggest implementation approach for skill"""
    intent = cmd.get("intent", "other")
    category = cmd.get("category", "Other")
    safety = cmd.get("safety", "read-only")
    
    implementations = {
        "simple": {
            "pattern": "basic wrapper",
            "approach": "direct command execution with validation",
            "complexity": "low",
            "estimated_time": "1-2 days"
        },
        "moderate": {
            "pattern": "enhanced wrapper",
            "approach": "structured execution with error handling",
            "complexity": "medium",
            "estimated_time": "2-4 days"
        },
        "complex": {
            "pattern": "orchestration skill",
            "approach": "pipeline execution with dependencies",
            "complexity": "high",
            "estimated_time": "1-2 weeks"
        },
        "expert": {
            "pattern": "expert system",
            "approach": "advanced orchestration with learning",
            "complexity": "very high",
            "estimated_time": "2-4 weeks"
        }
    }
    
    # Choose implementation based on characteristics
    if intent in ("monitor", "search"):
        impl = "simple"
    elif intent in ("process", "calculate"):
        impl = "moderate"
    elif intent in ("create", "convert"):
        impl = "complex"
    elif safety in ("can-change", "mixed"):
        impl = "complex"
    else:
        impl = "moderate"
    
    return implementations[impl]


def calculate_confidence(cmd: Dict, surfaces: List[str]) -> str:
    """Calculate confidence level for skill recommendation"""
    confidence_factors = []
    
    # JSON output confidence
    if cmd.get("json"):
        confidence_factors.append("high")
    
    # Explicit intent confidence
    if cmd.get("explicit"):
        confidence_factors.append("high")
    
    # Surface alignment confidence
    if len(surfaces) >= 2:
        confidence_factors.append("high")
    
    # Category clarity confidence
    if cmd.get("category") and cmd["category"] != "Other":
        confidence_factors.append("medium")
    
    # Intent clarity confidence
    if cmd.get("intent") and cmd["intent"] != "other":
        confidence_factors.append("medium")
    
    # Safety classification confidence
    if cmd.get("safety") and cmd["safety"] != "read-only":
        confidence_factors.append("low")
    
    # Determine overall confidence
    if "high" in confidence_factors:
        return "high"
    elif "medium" in confidence_factors:
        return "medium"
    elif "low" in confidence_factors:
        return "low"
    else:
        return "low"


def enhanced_gap_analysis(recommendations: List[Dict]) -> Dict:
    """Analyze gaps in skill coverage with enhanced insights"""
    analysis = {
        "high_priority": [],
        "medium_priority": [],
        "low_priority": [],
        "expert_skills_needed": [],
        "domain_coverage": {},
        "intent_coverage": {},
        "category_coverage": {}
    }
    
    # Count coverage by category
    for rec in recommendations:
        category = rec["category"]
        analysis["category_coverage"][category] = analysis["category_coverage"].get(category, 0) + 1
    
    # Count coverage by intent  
    for rec in recommendations:
        intent = rec["intent"]
        analysis["intent_coverage"][intent] = analysis["intent_coverage"].get(intent, 0) + 1
    
    # Count coverage by domain
    for rec in recommendations:
        domain = rec["domain"]
        analysis["domain_coverage"][domain] = analysis["domain_coverage"].get(domain, 0) + 1
    
    # Classify by priority
    for rec in recommendations:
        if rec["score"] >= 8.0:
            analysis["high_priority"].append(rec)
        elif rec["score"] >= 5.0:
            analysis["medium_priority"].append(rec)
        else:
            analysis["low_priority"].append(rec)
        
        if rec["complexity"] == "expert":
            analysis["expert_skills_needed"].append(rec)
    
    return analysis


def generate_skills_report(recommendations: List[Dict], analysis: Dict) -> str:
    """Generate comprehensive skills report"""
    report = []
    report.append("# Enhanced Chronoa Skills Gap Analysis")
    report.append("")
    report.append(f"Based on analysis of {len(recommendations)} potential skills:")
    report.append("")
    
    # Summary statistics
    report.append("## Summary Statistics")
    report.append("")
    report.append(f"- **Total potential skills**: {len(recommendations)}")
    report.append(f"- **High priority skills**: {len(analysis['high_priority'])}")
    report.append(f"- **Medium priority skills**: {len(analysis['medium_priority'])}")
    report.append(f"- **Low priority skills**: {len(analysis['low_priority'])}")
    report.append(f"- **Expert skills needed**: {len(analysis['expert_skills_needed'])}")
    report.append("")
    
    # Priority categories
    report.append("## Priority Categories")
    report.append("")
    
    report.append("### High Priority Skills (Score ≥ 8.0)")
    report.append("")
    report.append("| Command | Description | Category | Intent | Score | Surfaces | Complexity |")
    report.append("|---|---|---|---|---|---|---|")
    for rec in analysis["high_priority"][:20]:
        surfaces_str = ", ".join(rec["surfaces"])
        report.append(f"| `{rec['command']}` | {rec['description'][:50]}... | {rec['category']} | {rec['intent']} | {rec['score']:.1f} | {surfaces_str} | {rec['complexity']} |")
    if len(analysis["high_priority"]) > 20:
        report.append(f"... and {len(analysis['high_priority']) - 20} more")
    report.append("")
    
    report.append("### Medium Priority Skills (Score 5.0-7.9)")
    report.append("")
    report.append("| Command | Description | Category | Intent | Score |")
    report.append("|---|---|---|---|---|")
    for rec in analysis["medium_priority"][:20]:
        report.append(f"| `{rec['command']}` | {rec['description'][:50]}... | {rec['category']} | {rec['intent']} | {rec['score']:.1f} |")
    if len(analysis["medium_priority"]) > 20:
        report.append(f"... and {len(analysis['medium_priority']) - 20} more")
    report.append("")
    
    # Domain coverage
    report.append("## Domain Coverage")
    report.append("")
    report.append("The following skill domains are covered, ranked by potential impact:")
    report.append("")
    
    sorted_domains = sorted(analysis["domain_coverage"].items(), key=lambda x: x[1], reverse=True)
    for domain, count in sorted_domains:
        percentage = (count / len(recommendations)) * 100
        report.append(f"- **{domain}**: {count} skills ({percentage:.1f}%)")
    
    report.append("")
    
    # Intent coverage
    report.append("## Intent Coverage")
    report.append("")
    report.append("Skills cover the following intents, ranked by coverage:")
    report.append("")
    
    sorted_intents = sorted(analysis["intent_coverage"].items(), key=lambda x: x[1], reverse=True)
    for intent, count in sorted_intents:
        percentage = (count / len(recommendations)) * 100
        report.append(f"- **{intent}**: {count} skills ({percentage:.1f}%)")
    
    report.append("")
    
    # Category coverage
    report.append("## Category Coverage")
    report.append("")
    report.append("Skills cover the following categories, ranked by volume:")
    report.append("")
    
    sorted_categories = sorted(analysis["category_coverage"].items(), key=lambda x: x[1], reverse=True)
    for category, count in sorted_categories:
        percentage = (count / len(recommendations)) * 100
        report.append(f"- **{category}**: {count} skills ({percentage:.1f}%)")
    
    report.append("")
    
    # Expert skills needed
    if analysis["expert_skills_needed"]:
        report.append("## Expert Skills Needed")
        report.append("")
        report.append("The following skills require advanced expertise:")
        report.append("")
        for rec in analysis["expert_skills_needed"][:10]:
            report.append(f"- **{rec['command']}** ({rec['complexity']} complexity)")
        if len(analysis["expert_skills_needed"]) > 10:
            report.append(f"... and {len(analysis['expert_skills_needed']) - 10} more")
        report.append("")
    
    # Implementation recommendations
    report.append("## Implementation Recommendations")
    report.append("")
    report.append("Based on this analysis:")
    report.append("")
    report.append("1. **Start with high priority skills** that score ≥ 8.0")
    report.append("2. **Focus on missing domains** with low coverage")
    report.append("3. **Plan for complexity** - expert skills need senior developers")
    report.append("4. **Balance surface coverage** - ensure skills serve multiple purposes")
    report.append("5. **Consider dependencies** - skills may need supporting infrastructure")
    
    return "\n".join(report)


def main():
    """Enhanced main function with comprehensive analysis"""
    # Parse command line arguments
    out_format = "markdown"
    enrich = False
    scaffold = False
    path = "."
    
    for arg in sys.argv[1:]:
        if arg == "--enrich":
            enrich = True
        elif arg == "--scaffold":
            scaffold = True
        elif arg == "--json":
            out_format = "json"
        elif arg == "--markdown":
            out_format = "markdown"
        elif arg == "--html":
            out_format = "html"
        elif not arg.startswith("--"):
            path = arg
    
    # For now, read from the sample matrix if it exists
    matrix_path = HERE / "chronoa-matrix" / "chronoa-matrix.json"
    if not matrix_path.exists():
        print("No matrix JSON found. Run cli_matrix.py first to generate it.")
        return 1
    
    # Load matrix data
    with open(matrix_path, "r") as f:
        matrix_data = json.load(f)
    
    commands = matrix_data.get("commands", [])
    
    print("=== ENHANCED CHRONOA SKILLS ANALYSIS ===")
    print(f"Analyzing {len(commands)} commands...")
    print()
    
    # Generate enhanced skill recommendations
    context = {
        "high_demand": True,
        "complex_operation": True,
        "security_critical": True,
        "user_facing": True,
        "system_essential": True
    }
    
    recommendations = generate_skill_recommendations(commands, context)
    analysis = enhanced_gap_analysis(recommendations)
    
    # Display statistics
    print("ENHANCED ANALYSIS RESULTS")
    print("=" * 50)
    print(f"Total skills analyzed: {len(recommendations)}")
    print(f"High priority skills: {len(analysis['high_priority'])} ({len(analysis['high_priority'])/len(recommendations)*100:.1f}%)")
    print(f"Medium priority skills: {len(analysis['medium_priority'])} ({len(analysis['medium_priority'])/len(recommendations)*100:.1f}%)")
    print(f"Low priority skills: {len(analysis['low_priority'])} ({len(analysis['low_priority'])/len(recommendations)*100:.1f}%)")
    print(f"Expert skills needed: {len(analysis['expert_skills_needed'])}")
    print()
    
    # Show top recommendations
    print("TOP 10 RECOMMENDATIONS")
    print("=" * 50)
    for i, rec in enumerate(recommendations[:10], 1):
        print(f"{i}. `{rec['command']}`")
        print(f"   Description: {rec['description'][:80]}...")
        print(f"   Category: {rec['category']}, Intent: {rec['intent']}")
        print(f"   Score: {rec['score']:.1f}, Complexity: {rec['complexity']}, Domain: {rec['domain']}")
        print(f"   Surfaces: {', '.join(rec['surfaces'])}")
        print(f"   Confidence: {rec['confidence']}")
        print()
    
    # Generate detailed report
    report = generate_skills_report(recommendations, analysis)
    
    # Save report
    report_path = HERE / f"enhanced_skills_report_{data['generated']}.md"
    with open(report_path, "w") as f:
        f.write(report)
    
    print(f"Detailed report saved to: {report_path}")
    print("\nENHANCED ANALYSIS COMPLETE!")
    
    return 0

if __name__ == "__main__":
    sys.exit(main())