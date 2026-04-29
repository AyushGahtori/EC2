# Restaurant AI Agent

A multi-agent conversational AI system for restaurant order management, built with LangChain.

## Overview

This project uses a router-centric architecture with specialized agents for different parts of the ordering flow.

## System Architecture

### Core Components

**Router Agent (Central Hub)**

- Intent classification (greeting, ordering, browsing, and more)
- Item extraction from natural language
- Dynamic routing to specialized agents
- Clarification prompts for ambiguous input

**Specialized Agents**

- **Menu Agent**: Menu queries, recommendations, item information
- **Order Agent**: Order processing and modifications
- **Upselling Agent**: Complementary item suggestions
- **Finalization Agent**: Order completion and payment step handling
- **Delivery Agent**: Delivery or pickup handling

**Conversation Flow**

```text
START -> Router Agent -> Specialized Agents -> Conversation Stages -> Router Agent -> END
```

### Project Structure

```text
src/
|-- config.py                 # Configuration settings
|-- main.py                   # Main application entry point
|-- agents/                   # Specialized AI agents
|   |-- router_agent.py       # Central routing logic
|   |-- menu_agent.py         # Menu handling
|   |-- order_agent.py        # Order processing
|   `-- upselling_agent.py    # Upselling logic
|-- graph/                    # Conversation flow graph
|   `-- restaurant_graph.py   # Graph implementation
|-- models/                   # Data models
|   |-- menu_models.py        # Menu data structures
|   |-- order_models.py       # Order data structures
|   `-- shared_memory.py      # Shared state management
|-- data/                     # Configuration data
|   |-- menu.json             # Restaurant menu
|   `-- upselling_rules.json  # Upselling rules
|-- tools/                    # Utility functions
`-- prompts/                  # AI prompts
```

## Quick Start

### 1. Environment Setup

Create a `.env` file in the root directory:

```env
GOOGLE_API_KEY=your_google_api_key_here
```

### 2. Install Dependencies

```bash
pip install -r requirements.txt
```

### 3. Run the Application

```bash
python src/main.py
```

## Configuration

Edit `src/config.py` to customize model and behavior.

## Key Features

- **Intelligent Routing**: Context-aware conversation management
- **Smart Ordering**: Natural language item extraction (for example, `2 biryani and 3 chai`)
- **Dynamic Upselling**: Context-based recommendations
- **Multi-turn Conversations**: Maintains conversation context
- **Human Intervention**: Escalation path for complex queries
- **Order Analytics**: Basic order tracking and metadata
- **Flexible Flow**: Adaptive stage management

## Usage Examples

```python
from src.main import RestaurantAIAgent

agent = RestaurantAIAgent()

response = agent.chat("Hello! I'd like to see your menu")
print(response)

response = agent.chat("I want 2 biryani and a masala chai")
print(response)

response = agent.chat("I'll take delivery please")
print(response)
```

## Development

### Graph Visualization

```bash
jupyter notebook graph.ipynb
```
