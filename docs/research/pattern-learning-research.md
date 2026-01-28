# Pattern Learning Research Synthesis

> Research conducted January 2026 to identify best practices from analogous solutions for browser-use pattern learning system.

## Executive Summary

Our current pattern learning implementation provides a solid foundation but lacks several key features found in state-of-the-art web agent memory systems. This document synthesizes findings from 4 major research efforts and proposes concrete improvements.

**Key Gap**: Our system saves all patterns regardless of task outcome. Research shows **success-validated patterns** improve performance by 24-51%.

---

## Research Sources

### 1. WebCoach (arXiv:2511.12997)
**"WebCoach: Towards Effective Cross-Session Web Agent Coaching"**

Cross-session memory system that coaches web agents using past experiences.

**Architecture:**
- **WebCondenser**: Standardizes interaction logs into uniform format
- **External Memory Store**: Episodic memory of past sessions
- **Coach Module**: Retrieves relevant experiences and injects advice at runtime

**Key Results:**
- WebVoyager benchmark: 47% → 61% success rate (+14%)
- Works across different base models (GPT-4, Claude)

**Retrieval Strategy:**
- Similarity-based: Embed current task, find similar past tasks
- Recency-weighted: Newer experiences prioritized (websites change)
- Runtime injection: Advice injected into system prompt dynamically

### 2. Agent Workflow Memory (AWM) - ICML 2025
**"Agent Workflow Memory"**

Induces reusable "workflows" from successful trajectories.

**Key Innovation:**
- Abstracts specific actions into **reusable workflow templates**
- Works both **offline** (from training data) and **online** (during execution)

**Results:**
- Mind2Web: +24.6% improvement
- WebArena: +51.1% improvement

**Workflow Structure:**
```
Workflow: "Login to website"
Steps:
  1. Find username field → input username
  2. Find password field → input password  
  3. Find submit button → click
Applicability: Any site with standard login form
```

### 3. PRAXIS (arXiv:2511.22074)
**"PRAXIS: State-Dependent Memory for Web Agents"**

State-indexed memory matching both environment and internal state.

**Memory Entry Structure:**
```python
{
    "env_state_pre": {...},    # DOM state before action
    "internal_state": {...},   # Agent's reasoning/goals
    "action": {...},           # Action taken
    "env_state_post": {...},   # DOM state after action
    "success": bool            # Did it work?
}
```

**Retrieval:**
- IoU (Intersection over Union) for DOM state similarity
- Embedding similarity for internal state
- Combined score determines relevance

**Results:**
- Improves accuracy, reliability, and efficiency
- Works across different LLM backends

### 4. Awesome-Memory-for-Agents (Tsinghua Taxonomy)

Comprehensive taxonomy of agent memory systems.

**Three Categories:**

1. **Personalization Memory**
   - User profiles, preferences
   - Interaction history
   - Not directly applicable to browser-use

2. **Learning from Experience**
   - Trajectories (what we do)
   - Success/failure lessons (what we should add)
   - Reusable skills/workflows (AWM approach)

3. **Long-horizon Task Memory**
   - Intermediate results
   - Reasoning traces
   - Partial progress checkpoints

---

## Comparison Matrix

| Feature | Our System | WebCoach | AWM | PRAXIS |
|---------|------------|----------|-----|--------|
| **Storage** | JSON file | External DB | Workflow library | State-indexed DB |
| **Granularity** | Domain + type | Episode summary | Abstract workflow | State-action-result |
| **Retrieval** | Manual load | Similarity + recency | Task similarity | IoU + embedding |
| **Learning** | Manual save | Auto from trajectories | Offline/Online | Real-time |
| **Success validation** | ❌ | ✅ | ✅ | ✅ |
| **Multi-step patterns** | ❌ | ✅ | ✅ | ❌ |
| **State matching** | Domain only | Task embedding | Task embedding | DOM + internal |

---

## Recommended Improvements

### Priority 1: Success/Failure Validation (browser-use-euh)

**Problem:** We save all patterns, including failed attempts.

**Solution:**
```python
class PatternEntry(BaseModel):
    # ... existing fields ...
    success: bool = True  # NEW: Was this pattern from a successful task?
    failure_count: int = 0  # NEW: How many times did this fail?
```

**Implementation:**
1. Add `success` field to `PatternEntry`
2. Track task completion status in `PatternLearningAgent`
3. Only merge patterns when `agent.run()` completes successfully
4. Add `save_patterns(only_successful=True)` parameter

**Expected Impact:** +15-25% pattern quality based on WebCoach/AWM results.

### Priority 2: Auto-Learning Mode (browser-use-bpr)

**Problem:** Requires manual `save_patterns()` call.

**Solution:**
```python
agent = PatternLearningAgent(
    task="...",
    llm=llm,
    pattern_store=store,
    auto_learn=True,  # NEW: Auto-save on success
    auto_learn_threshold=0.8  # NEW: Min confidence to save
)
```

**Implementation:**
1. Add callback hook to `agent.run()` completion
2. Check task success status
3. Auto-save patterns if successful
4. Optional confidence threshold

### Priority 3: Workflow Pattern Type (browser-use-w9a)

**Problem:** Patterns are single actions; can't capture multi-step sequences.

**Solution:**
```python
class WorkflowPattern(BaseModel):
    name: str
    description: str
    steps: list[PatternEntry]
    applicability: str  # When to use this workflow
    
class PatternType(str, Enum):
    # ... existing types ...
    WORKFLOW = "workflow"  # NEW
```

**Implementation:**
1. Add `WorkflowPattern` model
2. Detect repeated action sequences
3. Abstract into reusable workflows
4. Match workflows by task similarity

**Expected Impact:** +24-51% based on AWM results for complex tasks.

### Priority 4: Similarity-Based Retrieval (browser-use-474)

**Problem:** LLM manually reads entire patterns.json; doesn't scale.

**Solution:**
```python
class PatternStore:
    def get_relevant_patterns(
        self,
        current_state: dict,
        top_k: int = 5
    ) -> list[PatternEntry]:
        # Embed current state
        # Find similar patterns by embedding distance
        # Return top-k most relevant
```

**Implementation:**
1. Add optional embedding model (sentence-transformers)
2. Embed pattern descriptions on save
3. Embed current page state on retrieval
4. Return top-k by cosine similarity

**Complexity:** Requires embedding dependency; defer to v2.1.

---

## Implementation Roadmap

### v2.0 (Immediate)
- [ ] browser-use-euh: Success/failure validation
- [ ] browser-use-bpr: Auto-learning mode

### v2.1 (Next Sprint)
- [ ] browser-use-w9a: Workflow pattern type
- [ ] browser-use-474: Similarity-based retrieval

### v3.0 (Future)
- [ ] State-indexed retrieval (PRAXIS approach)
- [ ] Cross-session coaching (WebCoach approach)
- [ ] Recency weighting for pattern ranking

---

## References

1. WebCoach: https://arxiv.org/abs/2511.12997
2. Agent Workflow Memory: ICML 2025 proceedings
3. PRAXIS: https://arxiv.org/abs/2511.22074
4. Awesome-Memory-for-Agents: https://github.com/Tsinghua/awesome-memory-for-agents

---

## Appendix: Code Snippets from Research

### WebCoach Retrieval (Pseudocode)
```python
def retrieve_advice(current_task, memory_store, k=3):
    task_embedding = embed(current_task)
    candidates = memory_store.search(task_embedding, k=k*2)
    
    # Recency weighting
    scored = []
    for c in candidates:
        age_days = (now() - c.timestamp).days
        recency_score = 1.0 / (1 + age_days * 0.1)
        similarity_score = cosine_sim(task_embedding, c.embedding)
        scored.append((c, similarity_score * recency_score))
    
    return sorted(scored, key=lambda x: -x[1])[:k]
```

### AWM Workflow Induction (Pseudocode)
```python
def induce_workflow(trajectories):
    # Find common action subsequences
    common_seqs = find_frequent_subsequences(trajectories)
    
    workflows = []
    for seq in common_seqs:
        # Abstract away specific selectors
        abstract_seq = [abstract_action(a) for a in seq]
        
        # Generate applicability condition
        applicability = generate_condition(seq.contexts)
        
        workflows.append(Workflow(
            steps=abstract_seq,
            applicability=applicability
        ))
    
    return workflows
```

### PRAXIS State Matching (Pseudocode)
```python
def match_state(current_dom, current_internal, memory):
    best_match = None
    best_score = 0
    
    for entry in memory:
        # DOM similarity via IoU
        dom_sim = iou(current_dom.elements, entry.env_state_pre.elements)
        
        # Internal state similarity via embedding
        internal_sim = cosine_sim(
            embed(current_internal),
            embed(entry.internal_state)
        )
        
        # Combined score
        score = 0.6 * dom_sim + 0.4 * internal_sim
        
        if score > best_score and entry.success:
            best_score = score
            best_match = entry
    
    return best_match
```
