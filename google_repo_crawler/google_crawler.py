import json
import requests
import xml.etree.ElementTree as ET
import subprocess
import os
import re
from pymongo import MongoClient
from datetime import datetime
import time
from dotenv import load_dotenv
from packaging import version  # for proper version comparison
#Get all necessary info and store it mongodb
# MongoDB connection setup (configure as needed)
load_dotenv()
MONGO_URI = os.getenv("MONGO_URI")
client = MongoClient(MONGO_URI)
DB_NAME = "maven_artifacts_google"
COLLECTION_NAME = "artifact_metadata4"

# Get the current directory where the script is located
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
ANDROID_PROJECT_DIR = SCRIPT_DIR
BUILD_GRADLE_PATH = os.path.join(ANDROID_PROJECT_DIR, "build.gradle")
GOOGLE_MAVEN_INDEX = "https://maven.google.com/master-index.xml"
GOOGLE_MAVEN_BASE = "https://maven.google.com/"

# Initialize MongoDB connection
def get_mongo_collection():
    client = MongoClient(MONGO_URI)
    db = client[DB_NAME]
    return db[COLLECTION_NAME]

def debug_print(message):
    print(f"🔍 DEBUG: {message}")

# ========== POM FETCHING AND PARSING FUNCTIONS ==========
def fetch_pom(group_id, artifact_id, version):
    """Fetch POM content from Google's Maven repository"""
    base_url = "https://dl.google.com/dl/android/maven2"
    group_path = group_id.replace('.', '/')
    pom_url = f"{base_url}/{group_path}/{artifact_id}/{version}/{artifact_id}-{version}.pom"

    response = requests.get(pom_url)
    if response.status_code == 200:
        return response.text
    else:
        raise Exception(f"Failed to fetch POM: HTTP {response.status_code}")

def parse_pom(pom_content):
    """Parse the POM XML and extract description, URL, and dependencies"""
    ns = {'m': 'http://maven.apache.org/POM/4.0.0'}
    root = ET.fromstring(pom_content)

    description = root.find('m:description', ns)
    url = root.find('m:url', ns)
    deps = []

    for dep in root.findall('m:dependencies/m:dependency', ns):
        g = dep.find('m:groupId', ns).text if dep.find('m:groupId', ns) is not None else ''
        a = dep.find('m:artifactId', ns).text if dep.find('m:artifactId', ns) is not None else ''
        v = dep.find('m:version', ns).text if dep.find('m:version', ns) is not None else ''
        deps.append(f"{g}:{a}:{v}")

    return (description.text if description is not None else '',
            url.text if url is not None else '',
            deps)

def fetch_aar_info(group_id, artifact_id, version):
    """Fetch AAR headers to get size and last modified date"""
    base_url = "https://dl.google.com/dl/android/maven2"
    group_path = group_id.replace('.', '/')
    aar_url = f"{base_url}/{group_path}/{artifact_id}/{version}/{artifact_id}-{version}.aar"
    jar_url =  f"{base_url}/{group_path}/{artifact_id}/{version}/{artifact_id}-{version}.jar"

    response = requests.head(aar_url)
    response1 = requests.head(jar_url)
    if response.status_code == 200:
        size = response.headers.get('Content-Length', 'Unknown')
        last_modified = response.headers.get('Last-Modified', 'Unknown')
        return size, last_modified
    elif response1.status_code == 200:
        size = response.headers.get('Content-Length', 'Unknown')
        last_modified = response.headers.get('Last-Modified', 'Unknown')
        return size, last_modified
    else:
        raise Exception(f"Failed to fetch AAR info: HTTP {response.status_code}")

# ========== GRADLE DEPENDENCY EXTRACTION FUNCTIONS ==========
def modify_gradle_build(group_id, artifact_id, version):
    if not os.path.exists(BUILD_GRADLE_PATH):
        debug_print(f"Error: build.gradle not found at {BUILD_GRADLE_PATH}")
        return False

    with open(BUILD_GRADLE_PATH, "r") as f:
        content = f.read()

    new_dependency = f"    implementation '{group_id}:{artifact_id}:{version}'"
    pattern = r"implementation\s+['\"][^'\"]+['\"]"
    new_content = re.sub(pattern, new_dependency, content)

    if new_content == content:
        if "dependencies {" in new_content:
            new_content = new_content.replace("dependencies {", f"dependencies {{\n{new_dependency}")
        else:
            new_content += f"\ndependencies {{\n{new_dependency}\n}}"

    with open(BUILD_GRADLE_PATH, "w") as f:
        f.write(new_content)

    debug_print("Successfully modified build.gradle")
    return True

def run_gradle_dependencies(output_file="dependencies.txt"):
    try:
        result = subprocess.run(
            ["./gradlew", "dependencies", "--configuration", "releaseRuntimeClasspath"],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=120
        )
        with open(output_file, "w", encoding="utf-8") as f:
            f.write(result.stdout if result.returncode == 0 else result.stderr)

        return output_file  # Return the path to the file

    except subprocess.TimeoutExpired:
        debug_print("Gradle command timed out after 120 seconds")
        with open(output_file, "w", encoding="utf-8") as f:
            f.write("Gradle command timed out after 120 seconds")
        return output_file
    except Exception as e:
        debug_print(f"Error running gradle: {e}")
        with open(output_file, "w", encoding="utf-8") as f:
            f.write(f"Error running gradle: {e}")
        return output_file

def parse_gradle_dependencies_file(file_path):
    """Read a dependencies file and extract cleaned first-level dependency lines"""
    cleaned_deps = []

    with open(file_path, 'r', encoding='utf-8') as f:
        for line in f:
            line = line.strip()
            if line.startswith('+---'):
                # Remove leading '+--- ' 
                dep_line = line[5:].strip()

                # Remove (*) if present
                dep_line = dep_line.replace('(*)', '').strip()

                # Handle '->' to take the right version
                if '->' in dep_line:
                    parts = dep_line.split('->')
                    left_part = parts[0].strip()
                    right_part = parts[1].strip()
                    # Replace left version with right version
                    # e.g., androidx.core:core:1.6.0 -> 1.9.0  becomes  androidx.core:core:1.9.0
                    if ':' in left_part:
                        group_artifact = ':'.join(left_part.split(':')[:-1])
                        dep_line = f"{group_artifact}:{right_part}"

                cleaned_deps.append(dep_line)

    return cleaned_deps

def get_direct_dependencies(group_id, artifact_id, version):
    target_dependency = f"{group_id}:{artifact_id}:{version}"

    if not modify_gradle_build(group_id, artifact_id, version):
        return []

    output_file = run_gradle_dependencies()
    direct_deps = parse_gradle_dependencies_file(output_file)

    # Filter to include only lines that actually belong to the target dependency
    filtered_deps = [dep for dep in direct_deps if target_dependency in dep or True]  # Keep all lines for now

    return filtered_deps

# ========== GOOGLE MAVEN INDEX FUNCTIONS ==========
def fetch_google_maven_artifacts():
    """Fetch all artifacts from Google's Maven repository"""
    GOOGLE_MAVEN_INDEX = "https://maven.google.com/master-index.xml"
    
    # Get the master index (list of groupIds)
    resp = requests.get(GOOGLE_MAVEN_INDEX)
    resp.raise_for_status()
    root = ET.fromstring(resp.content)

    artifacts = []
    
    for group_elem in root:
        group_id = group_elem.tag  # tag name is the groupId
        group_url = f"https://maven.google.com/{group_id.replace('.', '/')}/group-index.xml"

        g_resp = requests.get(group_url)
        if g_resp.status_code != 200:
            continue  # skip broken groups

        g_root = ET.fromstring(g_resp.content)
        for artifact_elem in g_root.findall("artifact"):
            artifact_id = artifact_elem.get("name")
            versions = artifact_elem.find("versions").text.split(",")
            latest_version = versions[-1].strip()
            artifacts.append({
                "group_id": group_id,
                "artifact_id": artifact_id,
                "latest_version": latest_version
            })
    
    return artifacts

# ========== MAIN PROCESSING FUNCTION ==========
# ========== MAIN PROCESSING FUNCTION ==========
def process_artifact(group_id, artifact_id, version):
    """Process a single artifact and store its metadata in MongoDB"""
    full_dependency_name = f"{group_id}:{artifact_id}:{version}"
    print(f"Processing {full_dependency_name}")
    
    try:
        # Fetch POM data
        pom_content = fetch_pom(group_id, artifact_id, version)
        description, url, direct_dependencies = parse_pom(pom_content)
        
        # Fetch AAR info
        size, last_modified = fetch_aar_info(group_id, artifact_id, version)
        
        # Get direct dependencies
        direct_dependencies = get_direct_dependencies(group_id, artifact_id, version)
        
        # Create document for MongoDB with full dependency name as _id
        artifact_data = {
            "_id": full_dependency_name,
            
            "description": description,
            "source codeurl": url,
            "jar_size": size,
            "last_modified": last_modified,
            
            "direct_dependencies": direct_dependencies
            
        }
        
        # Store in MongoDB
        collection = get_mongo_collection()
        
        # Use upsert to update if exists or insert if new
        result = collection.update_one(
            {"_id": full_dependency_name},
            {"$set": artifact_data},
            upsert=True
        )
        
        print(f"✅ Successfully processed and stored {full_dependency_name}")
        return full_dependency_name
        
    except Exception as e:
        print(f"❌ Error processing {full_dependency_name}: {str(e)}")
        return None
def fetch_artifact_versions(group_id, artifact_id):
    """Fetch all versions for a given artifact"""
    group_path = group_id.replace('.', '/')
    artifact_url = f"{GOOGLE_MAVEN_BASE}{group_path}/{artifact_id}/maven-metadata.xml"
    
    try:
        resp = requests.get(artifact_url, timeout=10)
        resp.raise_for_status()
        metadata_root = ET.fromstring(resp.content)
        
        # Extract all versions
        versions = []
        versioning = metadata_root.find('versioning')
        if versioning is not None:
            versions_elem = versioning.find('versions')
            if versions_elem is not None:
                for version_elem in versions_elem.findall('version'):
                    versions.append(version_elem.text)
        
        return versions
    except Exception as e:
        print(f"❌ Error fetching versions for {group_id}:{artifact_id}: {e}")
        return []

def get_latest_version(versions):
    """Determine the latest version from a list of version strings"""
    if not versions:
        return None
    
    # Filter out unusual version formats and try to parse
    valid_versions = []
    for v in versions:
        try:
            # Simple check for version format
            if any(char.isdigit() for char in v):
                valid_versions.append(v)
        except:
            continue
    
    if not valid_versions:
        return versions[0] if versions else None
    
    # Sort using version parsing for proper ordering
    try:
        valid_versions.sort(key=lambda x: version.parse(x))
        return valid_versions[-1]
    except:
        # Fallback: simple string sort if version parsing fails
        valid_versions.sort()
        return valid_versions[-1]

def fetch_group_artifacts(group_id):
    """Fetch all artifacts for a given group ID"""
    group_path = group_id.replace('.', '/')
    group_url = f"{GOOGLE_MAVEN_BASE}{group_path}/group-index.xml"
    
    try:
        resp = requests.get(group_url, timeout=10)
        resp.raise_for_status()
        group_root = ET.fromstring(resp.content)
        
        artifacts = []
        for artifact_elem in group_root:
            artifacts.append(artifact_elem.tag)
        
        return artifacts
    except Exception as e:
        print(f"❌ Error fetching artifacts for {group_id}: {e}")
        return []

def process_all_artifacts():
    """Stream through the master index, process and store each artifact immediately"""
    print("➡️ Fetching master index...")
    resp = requests.get(GOOGLE_MAVEN_INDEX)
    resp.raise_for_status()
    root = ET.fromstring(resp.content)
    print(f"✅ Master index fetched. Found {len(root)} groups. Processing artifacts...\n")

    processed_count = 0

    for i, group_elem in enumerate(root):
        

        group_id = group_elem.tag
        print(f"📦 Processing group ({i+1}): {group_id}")

        # Fetch artifacts for this group
        artifacts = fetch_group_artifacts(group_id)
        if not artifacts:
            print(f"   No artifacts found for {group_id}\n")
            continue

        print(f"   Found {len(artifacts)} artifacts")

        artifact_count = 0
        for artifact_id in artifacts:
            

            print(f"      Processing artifact: {artifact_id}")

            # Fetch latest version for this artifact
            versions = fetch_artifact_versions(group_id, artifact_id)
            latest_version = get_latest_version(versions)

            if latest_version:
                # Process and store immediately
                process_artifact(group_id, artifact_id, latest_version)
                processed_count += 1
            else:
                print(f"         No versions found for {artifact_id}")

            artifact_count += 1
            time.sleep(0.2)  # be respectful to the server

        print()

    print(f"✅ Processing complete. Total artifacts processed: {processed_count}")


# ========== INDIVIDUAL ARTIFACT PROCESSING ==========
def process_single_artifact(group_id, artifact_id, version):
    """Process a single specified artifact"""
    return process_artifact(group_id, artifact_id, version)

# Example usage
if __name__ == "__main__":
    # Process a single artifact
    #process_single_artifact("com.google.android.material", "material", "1.10.0")
    
    # Or process all artifacts (use with caution - this will take a long time)
    try:
        process_all_artifacts()  
    except Exception as e:
        print(f"Error occurred or program was interrupted: {e}")
    finally:
        collection = get_mongo_collection()
        # Export the database to a JSON file
        print("Exporting database to google_repo_dataset.json...")
        data = list(collection.find({},))

        with open("google_repo_dataset.json", "w") as f:
            json.dump(data, f, indent=2)
