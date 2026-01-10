#!/bin/bash
#
# Real-time monitoring script for Pipecat load testing
# Run this on your Azure VM during load tests
#
# Usage: ./load_test/monitor.sh
#

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

clear
echo -e "${BLUE}================================================${NC}"
echo -e "${BLUE}   PIPECAT LOAD TEST MONITOR${NC}"
echo -e "${BLUE}================================================${NC}"
echo ""
echo "Press Ctrl+C to stop monitoring"
echo ""

# Function to get container stats
get_container_stats() {
    docker stats --no-stream --format "table {{.Name}}\t{{.CPUPerc}}\t{{.MemUsage}}\t{{.MemPerc}}\t{{.NetIO}}" 2>/dev/null | grep -E "pipecat-agent|nginx"
}

# Function to get active sessions
get_active_sessions() {
    # Try each container
    for i in 1 2 3; do
        sessions=$(curl -s "http://localhost:800$i/health" 2>/dev/null | grep -o '"active_sessions":[0-9]*' | cut -d: -f2)
        if [ -n "$sessions" ]; then
            echo -n "$sessions "
        else
            echo -n "- "
        fi
    done
}

# Function to check for errors in logs
check_recent_errors() {
    docker-compose logs --tail=50 2>/dev/null | grep -iE "error|429|rate.?limit|failed|exception" | tail -5
}

# Main monitoring loop
while true; do
    clear
    echo -e "${BLUE}================================================${NC}"
    echo -e "${BLUE}   PIPECAT LOAD TEST MONITOR - $(date '+%H:%M:%S')${NC}"
    echo -e "${BLUE}================================================${NC}"
    echo ""

    # Container stats
    echo -e "${YELLOW}📦 CONTAINER RESOURCES${NC}"
    echo "----------------------------------------"
    get_container_stats
    echo ""

    # Active sessions per container
    echo -e "${YELLOW}📞 ACTIVE SESSIONS (per container)${NC}"
    echo "----------------------------------------"
    echo -n "Agent 1 | Agent 2 | Agent 3: "

    total_sessions=0
    for i in 1 2 3; do
        # Access via nginx or direct port if exposed
        response=$(curl -s --max-time 2 "http://pipecat-agent-$i:8000/health" 2>/dev/null || curl -s --max-time 2 "http://localhost:8000/health" 2>/dev/null)
        sessions=$(echo "$response" | grep -o '"active_sessions":[0-9]*' | cut -d: -f2)
        if [ -n "$sessions" ]; then
            echo -n "$sessions "
            total_sessions=$((total_sessions + sessions))
        else
            echo -n "? "
        fi
    done
    echo ""
    echo -e "Total active sessions: ${GREEN}$total_sessions${NC}"
    echo ""

    # Health check
    echo -e "${YELLOW}🏥 HEALTH STATUS${NC}"
    echo "----------------------------------------"
    health_response=$(curl -s --max-time 2 "http://localhost:8000/health" 2>/dev/null)
    if [ -n "$health_response" ]; then
        echo -e "${GREEN}✅ Nginx LB: Healthy${NC}"
        echo "$health_response" | python3 -m json.tool 2>/dev/null || echo "$health_response"
    else
        echo -e "${RED}❌ Nginx LB: Not responding${NC}"
    fi
    echo ""

    # Recent errors
    echo -e "${YELLOW}⚠️  RECENT ERRORS (last 5)${NC}"
    echo "----------------------------------------"
    errors=$(docker-compose logs --tail=100 2>/dev/null | grep -iE "error|429|rate.?limit|failed" | tail -5)
    if [ -n "$errors" ]; then
        echo -e "${RED}$errors${NC}"
    else
        echo -e "${GREEN}No recent errors${NC}"
    fi
    echo ""

    # Network connections
    echo -e "${YELLOW}🌐 NETWORK CONNECTIONS${NC}"
    echo "----------------------------------------"
    ws_connections=$(netstat -an 2>/dev/null | grep -c ":8000.*ESTABLISHED" || ss -an 2>/dev/null | grep -c ":8000.*ESTAB")
    echo "WebSocket connections on port 8000: $ws_connections"
    echo ""

    echo -e "${BLUE}================================================${NC}"
    echo "Refreshing in 3 seconds... (Ctrl+C to stop)"

    sleep 3
done
