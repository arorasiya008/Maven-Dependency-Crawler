import requests
import xmltodict
from pymongo import MongoClient
from bs4 import BeautifulSoup
from collections import OrderedDict
import time
import subprocess
import os
import re
from dotenv import load_dotenv
from urllib.parse import urljoin
from packaging import version  # helps compare versions properly
from datetime import datetime
import urllib.parse
import random

POM_TEMPLATE = """<project>
    <modelVersion>4.0.0</modelVersion>
    <groupId>temp-group</groupId>
    <artifactId>temp-artifact</artifactId>
    <version>1.0</version>
    <repositories>
        <repository>
            <id>cloudera-public</id>
            <url>https://repository.cloudera.com/artifactory/public/</url>
        </repository>
    </repositories>
    <dependencies>
        <dependency>
            <groupId>{{GROUP_ID}}</groupId>
            <artifactId>{{ARTIFACT_ID}}</artifactId>
            <version>{{VERSION}}</version>
        </dependency>
    </dependencies>
</project>"""

# Define the POM file path in the current directory
POM_FILE_PATH = os.path.join(os.getcwd(), "pom.xml")

# Create the POM file
with open(POM_FILE_PATH, "w") as pom_file:
    pom_file.write(POM_TEMPLATE)

print(f"POM file created at: {POM_FILE_PATH}")

# MongoDB Connection
load_dotenv()
MONGO_URI = os.getenv("MONGO_URI")
client = MongoClient(MONGO_URI)
db = client.cloudera_dependency_5
collection = db.cloudera_dependencies_5

# Cloudera URLs
CLOUDERA_REPO_URL = "https://repository.cloudera.com/repository/public/{}/{}/{}/{}-{}.pom"
CLOUDERA_DIRECTORY_URL = "https://repository.cloudera.com/service/rest/repository/browse/public/{}/{}/{}/"
BASE_URL = "https://repository.cloudera.com/service/rest/repository/browse/public/"

def normalize_timestamp(raw_timestamp):
    """
    Convert 'Tue Jan 30 19:41:11 UTC 2024' 
    → '2024-01-30 19:41'
    """
    try:
        # Parse with strptime (ignore the weekday and timezone)
        dt = datetime.strptime(raw_timestamp, "%a %b %d %H:%M:%S %Z %Y")
        # Reformat into desired format
        return dt.strftime("%Y-%m-%d %H:%M")
    except Exception:
        return raw_timestamp  # fallback if parsing fails

def fetch_last_modified_and_size(group_id, artifact_id, version):
    """Fetches timestamp and JAR size from the Maven directory listing, handling different JAR naming patterns."""
    time.sleep(0.2)  # Avoid throttling
    group_path = group_id.replace(".", "/")
    dir_url = CLOUDERA_DIRECTORY_URL.format(group_path, artifact_id, version)

    print(f"📂 Fetching directory: {dir_url}")
    response = requests.get(dir_url)
    if response.status_code != 200:
        return "Unknown", "Unknown"

    soup = BeautifulSoup(response.text, "html.parser")
    # Look for rows in the HTML table
    rows = soup.find_all("tr")
    timestamp, jar_size = "Unknown", "Unknown"

    for row in rows:
        cols = row.find_all("td")
        if not cols or len(cols) < 3:
            continue

        filename = cols[0].get_text(strip=True)
        if filename == f"{artifact_id}-{version}.jar":  # match exact jar
            timestamp = cols[1].get_text(strip=True)
            jar_size = cols[2].get_text(strip=True)
            print(normalize_timestamp(timestamp))
            return normalize_timestamp(timestamp), jar_size

    return timestamp, jar_size

def resolve_placeholder(value, properties, project):
    """Resolves ${variable} placeholders using the properties dictionary."""
    if value is None:
        return "Unknown"
    if value.startswith("${project.parent.") and value.endswith("}"):
        prop_name = value.replace("${project.parent", "").rstrip("}")
        return project.get("parent").get(prop_name, value)
    if value.startswith("${project.") and value.endswith("}"):
        prop_name = value.replace("${project.", "").rstrip("}")
        return project.get(prop_name, "")
    if isinstance(value, str) and value.startswith("${") and value.endswith("}"):
        prop_name = value.strip("${}")
        return properties.get(prop_name, value)  # Replace if found, else keep original

    return value

def fetch_pom(group_id, artifact_id, version):
    """Fetches the POM file content from Maven Central."""
    group_path = group_id.replace(".", "/")
    pom_url = CLOUDERA_REPO_URL.format(group_path, artifact_id, version, artifact_id, version)

    response = requests.get(pom_url)
    if response.status_code == 200:
        return response.text
    print(f"❌ POM not found for {group_id}:{artifact_id}:{version}")
    return None  # Return None if POM not found

def get_pom_properties(pom_xml, accumulated_properties):
    """
    Recursively fetches parent POM properties and merges them.
    - accumulated_properties keeps track of all merged properties from parent POMs.
    """
    if not pom_xml:
        return accumulated_properties

    try:
        pom_dict = xmltodict.parse(pom_xml)
        project = pom_dict["project"]

        # Extract and merge properties
        properties = project.get("properties", {})
        if isinstance(properties, dict):
            for key, value in properties.items():
                if key not in accumulated_properties:  # Preserve lowest-level properties
                    accumulated_properties[key] = value

        # Resolve placeholders in properties
        for key, value in accumulated_properties.items():
            value = resolve_placeholder(value, accumulated_properties, project)
            accumulated_properties[key] = value

        # Check if the parent has its own parent
        parent = project.get("parent")
        if parent:
            parent_group = resolve_placeholder(parent.get("groupId"), accumulated_properties, project)
            parent_artifact = resolve_placeholder(parent.get("artifactId"), accumulated_properties, project)
            parent_version = resolve_placeholder(parent.get("version"), accumulated_properties, project)
            parent_pom_xml = fetch_pom(parent_group, parent_artifact, parent_version)
            return get_pom_properties(parent_pom_xml, accumulated_properties)

    except Exception as e:
        print(f"⚠ Error parsing parent POM: {e}")

    return accumulated_properties

def modify_pom_file(group_id, artifact_id, version):
    """Replaces placeholders in the POM file with actual values."""
    with open(POM_FILE_PATH, "r") as file:
        content = file.read()

    content = content.replace("{{GROUP_ID}}", group_id)
    content = content.replace("{{ARTIFACT_ID}}", artifact_id)
    content = content.replace("{{VERSION}}", version)

    with open(POM_FILE_PATH, "w") as file:
        file.write(content)

def restore_pom_file(group_id, artifact_id, version):

    with open(POM_FILE_PATH, "w") as file:
        file.write(POM_TEMPLATE)

def get_transitive_dependencies(group_id, artifact_id, version):
    """Extracts transitive dependencies using the mvn dependency:tree command."""
    modify_pom_file(group_id, artifact_id, version)  # Replace placeholders with real values
    dependencies = []

    try:
        result = subprocess.run(
            ["mvn", "dependency:tree","-Ddepth=2", "-f", POM_FILE_PATH],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=30,
            shell=True,
        )

        if result.returncode == 0:
            clean_lines = [
                line.replace("[INFO] ", "", 1) if line.startswith("[INFO] ") else line
                for line in result.stdout.splitlines()
            ]
            for line in clean_lines:
                if "+- " in line or "\\- " in line:
                    parts = line.split(":")
                    depth = (len(line) - len(line.lstrip(" |"))) // 2
                    group_id = re.sub(r'^[^a-zA-Z0-9]+', '', parts[0])
                    artifact_id = parts[1]
                    version = parts[3]
                    scope = parts[4]
                    if(depth == 1):
                        dependency = f"{group_id}:{artifact_id}:{version}:{scope}"
                        dependencies.append(dependency)
        else:
            print(f"⚠ Error running mvn dependency:tree: {result.stderr}")
            dependencies = None  # Set to None if there's an error

    # except subprocess.TimeoutExpired:
    #     print(f"⚠ Timeout while extracting dependencies for {group_id}:{artifact_id}:{version}")
    except Exception as e:
        print(f"⚠ Error extracting dependencies: {e}")
        dependencies = None  # Set to None if there's an error
    restore_pom_file(group_id, artifact_id, version)
    return dependencies

def parse_pom(pom_xml, group_id, artifact_id, version):
    """Parses POM XML, extracts dependencies, and resolves properties from parent POMs."""
    properties = OrderedDict()  # Stores merged properties (including parent POM properties)
    child_modules = []
    parent_module = "Unknown"
    description = "Unknown"
    source_code_url = "Unknown"

    try:
        pom_dict = xmltodict.parse(pom_xml)
        
        if "project" in pom_dict:
            project = pom_dict["project"]

            properties = get_pom_properties(pom_xml, properties)

            # Extract parent module details
            parent = project.get("parent")
            if parent:
                parent_group_id = resolve_placeholder(parent.get("groupId"), properties, project)
                parent_artifact_id = resolve_placeholder(parent.get("artifactId"), properties, project)
                parent_version = resolve_placeholder(parent.get("version"), properties, project)
                if parent_group_id != "Unknown" and parent_artifact_id !="Unknown" and parent_version != "Unknown":
                    parent_module = f"{parent_group_id}:{parent_artifact_id}:{parent_version}"


            # Extract description
            description = resolve_placeholder(project.get("description"), properties, project)

            # Extract source code URL
            source_code_url = resolve_placeholder(project.get("scm", {}).get("url"), properties, project)

            # Extract child modules
            modules = project.get("modules", {}).get("module")
            if isinstance(modules, list):
                for module in modules:
                    child_modules.append(f"{group_id}:{module}:{version}")
            elif isinstance(modules, str):
                child_modules.append(f"{group_id}:{modules}:{version}")

        return description, source_code_url, parent_module, child_modules

    except Exception as e:
        print(f"⚠ Error parsing POM: {e}")
        print(description)
        print(child_modules)

    return description, source_code_url, parent_module, child_modules

def store_dependency(group_id, artifact_id, version, last_modified, jar_size, description, transitive_deps, source_code_url, parent_module, child_modules):
    """Stores dependency in MongoDB with last modified timestamp and JAR size."""
    dependency_id = f"{group_id}:{artifact_id}:{version}"

    # Check if the parent exists in the database
    if parent_module != "Unknown":
        parent_entry = collection.find_one({"_id": parent_module})

        if parent_entry:
            # Append current module to parent's child list if not already present
            collection.update_one(
                {"_id": parent_module},
                {"$addToSet": {"child_modules": dependency_id}}
            )
            print(f"Added {dependency_id} as a child to {parent_module}")

        else:
            # Use an existing field as a flag (e.g., description will be None if not processed)
            collection.insert_one({
                "_id": parent_module,
                "last_modified": None,
                "jar_size": None,
                "description": None,  # Flag: Description is None before processing
                "transitive_dependencies": [],
                "source_code_url": None,
                "parent_module": None,
                "child_modules": [dependency_id]
            })
            print(f"Created placeholder for unprocessed parent: {parent_module}")

    existing_entry = collection.find_one({"_id": dependency_id})
    if existing_entry:
        # Ensure new child modules are appended without duplication
        collection.update_one(
            {"_id": dependency_id},
            {
                "$set": {
                    "last_modified": last_modified,
                    "jar_size": jar_size,
                    "description": description,
                    "transitive_dependencies": transitive_deps,
                    "source_code_url": source_code_url,
                    "parent_module": parent_module
                },
                "$addToSet": {"child_modules": {"$each": child_modules}}
            }
        )
        print(f"✅ Updated DB: {dependency_id} (Last Modified: {last_modified}, Size: {jar_size})")
        
    else:
        # Insert a new entry if it doesn't exist
        collection.insert_one({
            "_id": dependency_id,
            "last_modified": last_modified,
            "jar_size": jar_size,
            "description": description,
            "transitive_dependencies": transitive_deps,
            "source_code_url": source_code_url,
            "parent_module": parent_module,
            "child_modules": child_modules
        })
        print(f"✅ Added to DB: {dependency_id} (Last Modified: {last_modified}, Size: {jar_size})")

def process_dependency(group_id, artifact_id, version):
    """Processes a single dependency and its transitive dependencies."""
    try:
        # Fetch last modified timestamp & JAR size
        last_modified, jar_size = fetch_last_modified_and_size(group_id, artifact_id, version)

        # Try fetching the POM
        pom_xml = fetch_pom(group_id, artifact_id, version)
        
        transitive_deps = []
        description = "Unknown"
        source_code_url = "Unknown"
        parent_module = "Unknown"
        child_modules = []

        if pom_xml:
            # Extract transitive dependencies using mvn dependency:tree
            transitive_deps = get_transitive_dependencies(group_id, artifact_id, version)

            # Parse the POM for other details
            description, source_code_url, parent_module, child_modules = parse_pom(pom_xml, group_id, artifact_id, version)

        # Store in MongoDB only if POM was found and transitive dependencies are resolved
        if pom_xml and transitive_deps is not None:
            store_dependency(group_id, artifact_id, version, last_modified, jar_size, description, transitive_deps, source_code_url, parent_module, child_modules)
            for dependency in transitive_deps:
                dep_parts = dependency.split(":")
                dep_group_id, dep_artifact_id, dep_version = dep_parts[:3]
                dependency_id = f"{dep_group_id}:{dep_artifact_id}:{dep_version}"
                # Check if the dependency exists
                print(f"🔍 Processing transitive dependency: {dependency_id}")
                if collection.find_one({"_id": dependency_id}):
                    print(f"Skipping (already processed): {dependency_id}")
                    continue  # Skip if already processed
                process_dependency(dep_group_id, dep_artifact_id, dep_version)
                
    except Exception as e:
        print(f"Failed to process {dep_group_id}:{dep_artifact_id}:{dep_version}: {e}")

def list_subdirs(url):
    """Return subdirectories from a Maven repo URL."""
    try:
        response = requests.get(url, timeout=10)
        if response.status_code != 200:
            return []
    except requests.RequestException:
        return []

    soup = BeautifulSoup(response.text, "html.parser")
    dirs = []
    for link in soup.find_all("a"):
        href = link.get("href")
        if href and href.endswith("/") and href not in ("../", "/"):
            dirs.append(urljoin(url, href))
    return dirs

def recurse_group(group_dir, depth):
    artifact_dirs = []
    if len(list_subdirs(group_dir)) == 0:
        print(f"Reached version directory: {group_dir}")
    elif depth < 5 and len(list_subdirs(group_dir)) > 0:
        for dir in list_subdirs(group_dir):
            if len(list_subdirs(dir)) > 0 and len(list_subdirs(list_subdirs(dir)[0])) > 0:
                print(f"Recursing into: {dir} at depth {depth}")
                artifact_dirs.extend(recurse_group(dir, depth + 1))
            else:
                print(f"Adding to artifact dirs: {dir}")
                artifact_dirs.append(dir)
    else:
        print(f"Max depth reached in: {group_dir}")
    
    return artifact_dirs

def get_all_dependencies(base=BASE_URL):
    """
    Crawl CloudEra repo and get only the latest version
    of each groupId:artifactId and process them.
    """
    # print(f"🌐 Starting crawl from: {base}")
    group_dirs = list_subdirs(base)
    for group_dir in group_dirs[398:]: # Restarting from 398 due to interruption
        if group_dir == base+".m2e/":
            continue  # Skip this directory
        # To handle nested groupIds, we need to go deeper
        artifact_dirs = recurse_group(group_dir, 0)

        artifact_indexes = random.sample(range(0, len(artifact_dirs)), min(100, len(artifact_dirs)))
        for index in artifact_indexes:
            artifact_dir = artifact_dirs[index]
            # Extract groupId and artifactId
            parts = artifact_dir.replace(BASE_URL, "").strip("/").split("/")
            group_id = ".".join(parts[:-1])
            artifact_id = parts[-1]

            # Collect versions
            versions = []
            for version in list_subdirs(artifact_dir):
                version_name = version.rstrip("/").split("/")[-1]       # get the last component
                version_name = urllib.parse.unquote(version_name)  # decode URL-encoded characters
                version_name = os.path.basename(version_name)      # ensure it's clean
                versions.append(version_name)

            if not versions:
                continue

            # Pick the latest version (semantic comparison)
            try:
                latest = str(max((version.parse(v) for v in versions)))
            except Exception:
                # fallback: lexicographic max if parsing fails
                latest = max(versions)

            if not (group_id.startswith("%23") or group_id.startswith("_") or group_id == ".."):
                dependency_id = f"{group_id}:{artifact_id}:{latest}"
                print(f"🔍 Processing: {dependency_id}")
                # Check if the dependency exists 
                if collection.find_one({"_id": dependency_id}):
                    print(f"🔍 Skipping (already processed): {dependency_id}")
                    continue  # Skip if already processed
                process_dependency(group_id, artifact_id, latest)

# Run the script

get_all_dependencies()
# print(len(list_subdirs(BASE_URL)))
# print(list_subdirs(BASE_URL).index(BASE_URL+"love/"))
# query = {"$or": [{"description": None}, {"description": {"$exists": False}}]}
# docs = collection.find({ "_id": { "$regex": "^ai." } })
# print(len(list(docs)))

# # Fetch results
# results = list(collection.find(query))
# print(f"Found {len(results)} unprocessed parent dependencies.")

# for doc in results:
#     dep_id = doc["_id"]
#     group_id, artifact_id, version = dep_id.split(":")[:3]
#     print(f"🔍 Processing unprocessed parent: {dep_id}")
#     process_dependency(group_id, artifact_id, version)

if os.path.exists(POM_FILE_PATH):
        os.remove(POM_FILE_PATH)
        print(f"Deleted temporary POM file: {POM_FILE_PATH}")
