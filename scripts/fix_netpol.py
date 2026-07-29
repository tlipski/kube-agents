#!/usr/bin/env python3
import subprocess
import os

PROJECT_ID = "tomeklipski-izrhgv"
REGION = "europe-west3"
MGMT_CLUSTER = "ka-dev-mgmt"
NAMESPACE = "kubeagents-system"

env = os.environ.copy()
env["CLOUDSDK_CORE_DISABLE_PROMPTS"] = "1"

netpol_yaml = f"""apiVersion: networking.k8s.io/v1
kind: NetworkPolicy
metadata:
  name: litellm-policy
  namespace: {NAMESPACE}
spec:
  podSelector:
    matchLabels:
      app: litellm
  policyTypes:
  - Ingress
  - Egress
  ingress:
  - from:
    - podSelector: {{}}
    ports:
    - protocol: TCP
      port: 4000
  egress:
  - to:
    - ipBlock:
        cidr: 0.0.0.0/0
    ports:
    - protocol: TCP
      port: 443
    - protocol: TCP
      port: 80
    - protocol: UDP
      port: 53
    - protocol: TCP
      port: 53
"""

def main():
    context_name = f"gke_{PROJECT_ID}_{REGION}_{MGMT_CLUSTER}"
    print("Updating litellm-policy NetworkPolicy to allow egress to Gemini API...")
    with open("/tmp/litellm-netpol.yaml", "w") as f:
        f.write(netpol_yaml)
    
    subprocess.run(["kubectl", "--context", context_name, "apply", "-f", "/tmp/litellm-netpol.yaml"], check=True, env=env)
    print("NetworkPolicy updated!")

if __name__ == "__main__":
    main()
