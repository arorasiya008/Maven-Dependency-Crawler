# Maven Dependency Crawler

This project automates the process of **fetching metadata** (last modified timestamp, JAR size, description, source code url, parent module & child modules) and **resolving transitive dependencies** for Maven artifacts.

---

## 📌 Methodology  

### **Fetching Last Modified Timestamp & JAR Size**  
1. **Construct the Directory URL** for each dependency by converting its `groupId`, `artifactId`, and `version` into a Maven repository path.  
2. **Request the HTML directory listing** of the dependency’s repository location.  
3. **Parse the `<pre>` tag**, which contains file metadata, using an HTML parser.  
4. **Extract the first timestamp** found in the directory listing.  
5. **Extract the JAR size** from the line that matches known file patterns (e.g., `.jar`, `.aar`).  

---

### **Fetching POM files**
1. **Construct the appropriate URL based on the artifact's `groupId`, `artifactId`, and `version`.
2. **Send a request to the Maven Central Repository and return the POM content if found.
3. **Return an error message if not found.

   ---

### **Fetching all dependencies**
1. **Start with an initial index (start=0) and fetches dependencies in batches.
2. **Send a request to the Maven Search API to retrieve dependency metadata.
3. **Parse the response and extract `groupId`, `artifactId`, and `version` for each dependency.
4. **Skip dependencies that have already been processed.
5. **Call `process_dependency` to fetch and store metadata.
6. **Iterates through pages until no more data is available, respecting API rate limits.

    ---

### **Fetching Transitive Dependencies**  
1. **Create a `pom.xml` file** in the current working directory.  
2. **Replace placeholders** in `pom.xml` with the actual `groupId`, `artifactId`, and `version` for each dependency.  
3. **Run the command**:  
   ```sh
   mvn dependency:tree -f pom.xml
4. **This generates a dependency tree**, which is then parsed to extract transitive dependencies.
5. **Restore** `pom.xml` to its original state (with placeholders instead of actual values).
6. **Once all dependencies are processed, delete** `pom.xml` to clean up.

---

### **Extracting Description**
1. Extracted from the `<description>` tag in `pom.xml`.  
2. If no description is provided, it is stored as `Unknown`.

---

### **Extracting Source Code URL**
1. Extracted from the `<scm><url>` tag in `pom.xml`.  
2. Provides a link to the project's source repository (e.g., **GitHub, GitLab, or Bitbucket**).  

---

### **Extracting Parent Module**
1. Extracted from the `<parent>` section of `pom.xml`.  
2. The **parent’s `groupId`, `artifactId`, and `version`** are combined to form the **parent module ID**.   

---

### **Extracting Child Modules**
1. Extracted from the `<modules>` section of `pom.xml`.  
2. If a module **explicitly defines its child modules**, they are **stored in the database**.  
3. If the `<modules>` section is **missing**, child modules are **inferred based on parent-child relationships**
- If a dependency declares a **parent**, then that dependency is added to **`child_modules`** of its parent

#### **Handling Missing Parent Modules**  
When a module declares a **parent** that **hasn't been processed yet**, a **placeholder entry** is created in the database:  
- **Parent `_id` is stored** with an **empty metadata structure**.  
- The **current dependency is added to `child_modules`** of this placeholder.  
- This placeholder is **later updated** when the parent module is processed.  

#### **Avoiding Duplicates in Child Modules**  
- MongoDB’s **`$addToSet`** operator ensures that a child module is **only added once** to the `child_modules` list.  
- This prevents redundant entries and ensures **accurate tracking of parent-child relationships**.  

---

### **Resolving Properties in POM**

Maven projects often use **properties (`${property.name}`)** to define reusable values, such as versions or repository URLs. These properties can be defined in multiple places, and resolving them correctly ensures that dependencies are processed with the correct versions and configurations. 

1. **Extract Properties from `<properties>` section** of the **current** `pom.xml`. 
2. **Recursively Extract Properties from Parent POMs**. This continues **recursively** until reaching the **root POM**.
3. As we move **up the hierarchy**, properties from **each parent POM** are added to an **accumulated dictionary**.  
4. If a property is **defined in both a child and its parent**, the **child’s value takes precedence** over the parent’s value.  
5. After processing all parent POMs, the final **accumulated properties dictionary is returned**.  
6. **All `${property}` placeholders** in the extracted attributes are **replaced with their resolved values**.  

#### **Handling `project.*` Placeholders**  
1. Some properties are prefixed with `project.` (e.g., `${project.version}`).  
2. These are **mapped directly to existing properties**:
  - `${project.version}` → `<version>` from `pom.xml`.
  - `${project.groupId}` → `<groupId>` from `pom.xml`.
  - `${project.artifactId}` → `<artifactId>` from `pom.xml`.  

#### **Handling `project.parent.*` Placeholders**  
1. Some properties are prefixed with `project.parent.` (e.g., `${project.parent.version}`).  
2. These are **mapped directly to existing properties**:
  - `${project.parent.version}` → `<version>` from `pom.xml`.
  - `${project.groupId}` → `<groupId>` from `pom.xml`.
  - `${project.artifactId}` → `<artifactId>` from `pom.xml`. 


