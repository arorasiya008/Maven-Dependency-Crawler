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

POM_TEMPLATE = """<project>
    <modelVersion>4.0.0</modelVersion>
    <groupId>temp-group</groupId>
    <artifactId>temp-artifact</artifactId>
    <version>1.0</version>
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
db = client.maven_dependency
collection = db.maven_dependencies

# Maven URLs
MAVEN_REPO_URL = "https://repo.maven.apache.org/maven2/{}/{}/{}/{}-{}.pom"
MAVEN_DIRECTORY_URL = "https://repo.maven.apache.org/maven2/{}/{}/{}/"
MAVEN_SEARCH_API = "https://search.maven.org/solrsearch/select?q=*:*&rows=100&start={}&wt=json"

def fetch_last_modified_and_size(group_id, artifact_id, version):
    """Fetches timestamp and JAR size from the Maven directory listing, handling different JAR naming patterns."""
    time.sleep(0.2)  # Avoid throttling
    group_path = group_id.replace(".", "/")
    dir_url = MAVEN_DIRECTORY_URL.format(group_path, artifact_id, version)

    print(f"📂 Fetching directory: {dir_url}")
    response = requests.get(dir_url)
    if response.status_code != 200:
        return "Unknown", "Unknown"

    soup = BeautifulSoup(response.text, "html.parser")
    pre_tag = soup.find("pre")

    if not pre_tag:
        return "Unknown", "Unknown"

    lines = pre_tag.text.strip().split("\n")
    print(f"🔍 Found {len(lines)} files in directory listing")

    # Extended JAR naming patterns
    jar_patterns = [
        f"{artifact_id}-{version}.jar",
        f"{artifact_id}-{version}.aar"
    ]
    timestamp="Unknown"
    jar_size="Unknown"

    href_dict = {}

    for link, line in zip(pre_tag.find_all("a"), lines):
        href = link.get("href")
        if href:
            href_dict[href] = line  # Map href to the corresponding line
     
    for key, value in href_dict.items():
        parts = value.split()
        if len(parts)>=4 :
            timestamp = f"{parts[1]} {parts[2]}"
            break
            
    # Extended JAR naming patterns
    jar_patterns = [
        f"{artifact_id}-{version}.jar",
        f"{artifact_id}-{version}.aar"
    ]

    for i in jar_patterns:
        for key, value in href_dict.items():
            if(key==i):
                parts = value.split()
                jar_size=parts[3]
                return timestamp, jar_size
        
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
    pom_url = MAVEN_REPO_URL.format(group_path, artifact_id, version, artifact_id, version)
    
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
            dep_group_id, dep_artifact_id, dep_version = dep_parts
            process_dependency(dep_group_id, dep_artifact_id, dep_version)

def get_all_dependencies():
    """Fetches dependencies from Maven Central and processes them."""
    start = 0
    rows = 100  # Number of dependencies per request

    while True:
        url = MAVEN_SEARCH_API.format(start)
        response = requests.get(url)
        
        if response.status_code != 200:
            print(f"❌ Error fetching data from Maven Central at start={start}")
            break

        data = response.json()
        docs = data.get("response", {}).get("docs", [])

        if not docs:
            break  # No more data

        for doc in docs:
            group_id = doc.get("g")
            artifact_id = doc.get("a")
            version = doc.get("latestVersion", "LATEST")
            dependency_id = f"{group_id}:{artifact_id}:{version}"
            # Check if the dependency exists 
            if collection.find_one({"_id": dependency_id}):
                continue  # Skip if already processed
            print(f"🔍 Processing: {dependency_id}")
            process_dependency(group_id, artifact_id, version)

        start += rows  # Move to the next page
        time.sleep(1)  # Respect API rate limits

# Run the script
get_all_dependencies()
if os.path.exists(POM_FILE_PATH):
        os.remove(POM_FILE_PATH)
        print(f"Deleted temporary POM file: {POM_FILE_PATH}")
