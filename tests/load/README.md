# Pipecat Load Testing Guide

## Quick Start

### Step 1: Prerequisites

Make sure you have:
- Python 3.11+ with `websockets` package
- Access to your Azure VM (SSH)
- Docker running on Azure VM

```bash
# Install websockets if not already installed
pip install websockets
```

### Step 2: Run the Load Test

#### Option A: Test Against Local Server (Development)
```bash
# Start your local server first
python bot.py

# In another terminal, run load test
python load_test/load_tester.py --url ws://localhost:8000/ws --calls 10 --duration 30
```

#### Option B: Test Against Azure Production
```bash
# Replace YOUR_AZURE_IP with your actual Azure VM IP
python load_test/load_tester.py --url ws://YOUR_AZURE_IP:8000/ws --calls 10 --duration 30
```

---

## Step-by-Step Testing Procedure

### Phase 1: Baseline Test (5 calls)

This establishes a baseline for comparison.

```bash
python load_test/load_tester.py --url ws://YOUR_AZURE_IP:8000/ws \
    --calls 5 \
    --duration 30 \
    --ramp-up 5 \
    --verbose
```

**Expected Results:**
- 100% success rate
- Connect time < 2s
- First audio < 8s

**Record these metrics before proceeding!**

---

### Phase 2: Light Load (15 calls)

```bash
python load_test/load_tester.py --url ws://YOUR_AZURE_IP:8000/ws \
    --calls 15 \
    --duration 45 \
    --ramp-up 10
```

**Watch for:**
- Success rate should still be 95%+
- Connect times may increase slightly
- Monitor Azure VM CPU (should be < 70%)

---

### Phase 3: Medium Load (25 calls)

```bash
python load_test/load_tester.py --url ws://YOUR_AZURE_IP:8000/ws \
    --calls 25 \
    --duration 60 \
    --ramp-up 15
```

**Watch for:**
- Any 429 rate limit errors
- Connect time degradation
- Memory usage on containers

---

### Phase 4: Target Load (40 calls)

This is your target capacity!

```bash
python load_test/load_tester.py --url ws://YOUR_AZURE_IP:8000/ws \
    --calls 40 \
    --duration 60 \
    --ramp-up 20
```

**Critical metrics to record:**
- Success rate (should be > 90%)
- Average connect time
- Peak concurrent connections
- Any errors

---

### Phase 5: Stress Test (50 calls)

Find your breaking point.

```bash
python load_test/load_tester.py --url ws://YOUR_AZURE_IP:8000/ws \
    --calls 50 \
    --duration 60 \
    --ramp-up 25
```

**This may fail - that's expected!** We want to know where the system breaks.

---

## Monitoring During Tests

### On Azure VM (SSH into your VM)

Open 3 terminal windows:

**Terminal 1: Container Stats**
```bash
watch -n 2 'docker stats --no-stream --format "table {{.Name}}\t{{.CPUPerc}}\t{{.MemUsage}}"'
```

**Terminal 2: Active Sessions**
```bash
watch -n 2 'curl -s http://localhost:8000/health | jq ".active_sessions"'
```

**Terminal 3: Error Logs**
```bash
docker-compose logs -f --tail=50 2>&1 | grep -iE "error|429|rate|limit|fail"
```

### Using the Monitor Script
```bash
chmod +x load_test/monitor.sh
./load_test/monitor.sh
```

---

## Understanding Results

### Success Rate Thresholds

| Success Rate | Status | Action |
|--------------|--------|--------|
| 95-100% | ✅ Excellent | System handles load well |
| 90-95% | ⚠️ Good | Minor issues, investigate errors |
| 80-90% | ⚠️ Warning | Approaching capacity |
| < 80% | ❌ Failing | Over capacity, reduce load |

### Connect Time Thresholds

| Connect Time | Status |
|--------------|--------|
| < 1s | ✅ Excellent |
| 1-3s | ⚠️ Acceptable |
| 3-5s | ⚠️ Degraded |
| > 5s | ❌ Too slow |

### Common Error Types

| Error | Cause | Solution |
|-------|-------|----------|
| `429 Too Many Requests` | API rate limit hit | Upgrade API plan or reduce calls |
| `Connection refused` | Server overloaded | Scale up resources |
| `Connection timeout` | Network or server issues | Check VM resources |
| `WebSocket closed` | Call ended or error | Check logs for details |

---

## Recording Results

Create a table for each test:

```
| Test Phase | Calls | Success | Avg Connect | Max Connect | Errors |
|------------|-------|---------|-------------|-------------|--------|
| Baseline   | 5     | 100%    | 0.8s        | 1.2s        | 0      |
| Light      | 15    | 100%    | 1.1s        | 2.0s        | 0      |
| Medium     | 25    | 96%     | 1.5s        | 3.2s        | 1      |
| Target     | 40    | 92%     | 2.1s        | 4.5s        | 3      |
| Stress     | 50    | 78%     | 3.5s        | 8.0s        | 11     |
```

---

## Troubleshooting

### High Failure Rate
1. Check container CPU usage (`docker stats`)
2. Check for 429 errors in logs
3. Verify API keys are valid
4. Check Azure VM resources

### Slow Connect Times
1. Check nginx load balancer
2. Verify network latency to Azure
3. Check container health

### Memory Issues
1. Check `docker stats` for memory usage
2. Look for memory leaks in logs
3. Consider increasing container limits

---

## Next Steps After Testing

If 40 calls PASS:
- Your system is ready for production
- Consider buffer (aim for 50 calls if expecting 40)

If 40 calls FAIL:
1. Identify bottleneck (CPU, memory, API limits)
2. Options:
   - Scale up Azure VM
   - Add more containers
   - Upgrade API plans
   - Optimize code

---

## Command Reference

```bash
# Full options
python load_test/load_tester.py \
    --url ws://YOUR_IP:8000/ws \  # Target URL
    --calls 40 \                   # Number of concurrent calls
    --duration 60 \                # Call duration in seconds
    --ramp-up 20 \                 # Time to start all calls
    --verbose                      # Show detailed logs
```
