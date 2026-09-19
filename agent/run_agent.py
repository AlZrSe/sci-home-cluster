#!/usr/bin/env python3
"""
Worker Agent for Scientific Home Cluster
"""

import argparse
import sys

def main():
    parser = argparse.ArgumentParser(description='Scientific Home Cluster Worker Agent')
    parser.add_argument('--node-id', required=True, help='Unique identifier for this node')
    parser.add_argument('--syncthing-root', required=True, help='Path to Syncthing shared folder')
    args = parser.parse_args()

    print(f"Starting agent for node {args.node_id}")
    print(f"Syncthing root: {args.syncthing_root}")
    # TODO: Implement agent logic

if __name__ == '__main__':
    sys.exit(main())