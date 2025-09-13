#!/usr/bin/env python3
"""
Yggdrasil Smart Peer Finder
Automatically finds the best nearby Yggdrasil peers by testing regions first
"""

import socket
import time
import re
import sys
import ssl
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
import json
import urllib.request
from html.parser import HTMLParser
import argparse


class PeerHTMLParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.peers_by_country = {}
        self.current_country = None
        self.in_country_header = False
        self.in_address_cell = False
        self.in_status_cell = False
        self.in_reliability_cell = False
        self.current_peer = {}

    def handle_starttag(self, tag, attrs):
        attrs_dict = dict(attrs)

        if tag == "th" and attrs_dict.get("id") == "country":
            self.in_country_header = True
        elif tag == "td" and attrs_dict.get("id") == "address":
            self.in_address_cell = True
        elif tag == "td" and attrs_dict.get("id") == "status":
            self.in_status_cell = True
        elif tag == "td" and attrs_dict.get("id") == "reliability":
            self.in_reliability_cell = True
        elif tag == "tr":
            class_attr = attrs_dict.get("class", "")
            if "statusgood" in class_attr or "statusavg" in class_attr:
                self.current_peer = {"status_class": class_attr}

    def handle_data(self, data):
        data = data.strip()
        if not data:
            return

        if self.in_country_header:
            self.current_country = data
            if self.current_country not in self.peers_by_country:
                self.peers_by_country[self.current_country] = []
        elif self.in_address_cell and data.startswith(
            ("tcp://", "tls://", "ws://", "wss://", "quic://")
        ):
            self.current_peer["address"] = data
        elif self.in_status_cell:
            self.current_peer["status"] = data
        elif self.in_reliability_cell and data.endswith("%"):
            self.current_peer["reliability"] = data
            if (
                self.current_country
                and "address" in self.current_peer
                and "status" in self.current_peer
                and "statusgood" in self.current_peer.get("status_class", "")
            ):
                self.peers_by_country[self.current_country].append(
                    self.current_peer.copy()
                )
            self.current_peer = {}

    def handle_endtag(self, tag):
        if tag == "th":
            self.in_country_header = False
        elif tag == "td":
            self.in_address_cell = False
            self.in_status_cell = False
            self.in_reliability_cell = False


class SmartPeerTester:
    def __init__(self, verbose=True):
        self.verbose = verbose
        self.results = []
        self.lock = threading.Lock()

    def log(self, message):
        if self.verbose:
            print(message)

    def fetch_peers(self):
        """Fetch and parse peers from the public peers website"""
        try:
            self.log("Fetching peers from publicpeers.neilalexander.dev...")

            ssl_context = ssl.create_default_context()
            ssl_context.check_hostname = False
            ssl_context.verify_mode = ssl.CERT_NONE

            req = urllib.request.Request(
                "https://publicpeers.neilalexander.dev/",
                headers={
                    "User-Agent": "Mozilla/5.0 (compatible; YggdrasilPeerFinder/1.0)"
                },
            )

            try:
                with urllib.request.urlopen(
                    req, timeout=15, context=ssl_context
                ) as response:
                    html_content = response.read().decode("utf-8")
            except Exception as ssl_error:
                self.log(f"HTTPS failed ({ssl_error}), trying HTTP...")
                req = urllib.request.Request(
                    "http://publicpeers.neilalexander.dev/",
                    headers={
                        "User-Agent": "Mozilla/5.0 (compatible; YggdrasilPeerFinder/1.0)"
                    },
                )
                with urllib.request.urlopen(req, timeout=15) as response:
                    html_content = response.read().decode("utf-8")

            parser = PeerHTMLParser()
            parser.feed(html_content)

            peers_by_country = {
                country: peers
                for country, peers in parser.peers_by_country.items()
                if peers and len(peers) > 0
            }

            total_peers = sum(len(peers) for peers in peers_by_country.values())
            self.log(
                f"Found {total_peers} online peers across {len(peers_by_country)} countries/regions"
            )

            return peers_by_country

        except Exception as e:
            print(f"Error fetching peers: {e}")
            return self.get_fallback_peers()

    def get_fallback_peers(self):
        """Fallback peer list if website fetch fails"""
        print("Using fallback peer list...")
        return {
            "united-states": [
                {"address": "tls://ygg.jjolly.dev:3443", "reliability": "100%"},
                {"address": "tls://23.184.48.86:993", "reliability": "100%"},
                {"address": "tls://44.234.134.124:443", "reliability": "100%"},
                {
                    "address": "tcp://mo.us.ygg.triplebit.org:9000",
                    "reliability": "100%",
                },
                {"address": "tls://mo.us.ygg.triplebit.org:993", "reliability": "100%"},
            ],
            "germany": [
                {"address": "tls://ygg.mkg20001.io:443", "reliability": "100%"},
                {"address": "tcp://ygg.mkg20001.io:80", "reliability": "100%"},
                {"address": "tls://yggdrasil.su:62586", "reliability": "100%"},
                {"address": "tcp://yggdrasil.su:62486", "reliability": "100%"},
            ],
            "netherlands": [
                {"address": "tls://vpn.itrus.su:7992", "reliability": "100%"},
                {"address": "tcp://vpn.itrus.su:7991", "reliability": "100%"},
                {"address": "tls://23.137.249.65:444", "reliability": "100%"},
            ],
            "france": [
                {"address": "tls://s2.i2pd.xyz:39575", "reliability": "100%"},
                {"address": "tcp://s2.i2pd.xyz:39565", "reliability": "100%"},
                {"address": "tls://51.15.204.214:54321", "reliability": "100%"},
            ],
        }

    def parse_peer_url(self, url):
        """Parse a peer URL to extract protocol, host, port"""
        ipv6_match = re.match(r"(\w+)://\[([^\]]+)\]:(\d+)(\?.*)?", url)
        if ipv6_match:
            return ipv6_match.group(1), ipv6_match.group(2), int(ipv6_match.group(3))

        match = re.match(r"(\w+)://([^:]+):(\d+)(\?.*)?", url)
        if match:
            return match.group(1), match.group(2), int(match.group(3))

        return None, None, None

    def test_connection(self, host, port, protocol="tcp", timeout=1):
        """Test connection and measure latency"""
        try:
            start = time.time()

            if protocol == "tls" or protocol == "wss":
                context = ssl.create_default_context()
                context.check_hostname = False
                context.verify_mode = ssl.CERT_NONE

                if ":" in host:
                    sock = socket.socket(socket.AF_INET6, socket.SOCK_STREAM)
                else:
                    try:
                        socket.getaddrinfo(host, port, socket.AF_INET6)
                        sock = socket.socket(socket.AF_INET6, socket.SOCK_STREAM)
                    except:
                        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)

                sock.settimeout(timeout)
                with context.wrap_socket(sock, server_hostname=host) as ssock:
                    ssock.connect((host, port))
            else:
                if ":" in host:
                    sock = socket.socket(socket.AF_INET6, socket.SOCK_STREAM)
                else:
                    try:
                        socket.getaddrinfo(host, port, socket.AF_INET6)
                        sock = socket.socket(socket.AF_INET6, socket.SOCK_STREAM)
                    except:
                        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)

                sock.settimeout(timeout)
                sock.connect((host, port))
                sock.close()

            latency = (time.time() - start) * 1000
            return True, latency

        except Exception:
            return False, None

    def test_single_peer(self, peer_data, country):
        """Test a single peer and return result"""
        peer_url = peer_data["address"]
        protocol, host, port = self.parse_peer_url(peer_url)

        if not protocol or not host or not port or protocol == "quic":
            return None

        success, latency = self.test_connection(host, port, protocol, timeout=1)

        if success:
            return {
                "url": peer_url,
                "latency": latency,
                "country": country,
                "reliability": peer_data.get("reliability", "N/A"),
                "protocol": protocol,
            }
        return None

    def test_region_fast(self, country, peers, sample_size=5):
        """Test multiple peers from a region simultaneously"""
        sample_peers = peers[: min(sample_size, len(peers))]
        results = []

        with ThreadPoolExecutor(max_workers=10) as executor:
            future_to_peer = {
                executor.submit(self.test_single_peer, peer, country): peer
                for peer in sample_peers
            }

            for future in as_completed(future_to_peer, timeout=3):
                try:
                    result = future.result()
                    if result:
                        results.append(result)
                except:
                    continue

        if results:
            avg_latency = sum(r["latency"] for r in results) / len(results)
            min_latency = min(r["latency"] for r in results)
            return avg_latency, min_latency, len(results)
        return float("inf"), float("inf"), 0

    def find_best_region(self, peers_by_country):
        """Test all regions simultaneously to find the best one"""
        self.log("\nTesting all regions simultaneously (5 peers per region)...")
        self.log("=" * 60)

        regional_scores = []

        with ThreadPoolExecutor(max_workers=20) as executor:
            future_to_country = {
                executor.submit(self.test_region_fast, country, peers): country
                for country, peers in peers_by_country.items()
                if len(peers) >= 2
            }

            for future in as_completed(future_to_country, timeout=10):
                try:
                    country = future_to_country[future]
                    avg_latency, min_latency, successful_tests = future.result()

                    if successful_tests > 0:
                        regional_scores.append(
                            (
                                country,
                                avg_latency,
                                min_latency,
                                successful_tests,
                                len(peers_by_country[country]),
                            )
                        )
                        self.log(
                            f"  {country}: {avg_latency:.1f}ms avg, {min_latency:.1f}ms min ({successful_tests}/{min(5, len(peers_by_country[country]))} successful)"
                        )
                    else:
                        self.log(f"  {country}: No successful connections")
                except Exception as e:
                    self.log(f"  {future_to_country[future]}: Test failed")

        if not regional_scores:
            self.log("No regions found with working peers!")
            return None

        # Sort by minimum latency first, then by average latency
        regional_scores.sort(key=lambda x: (x[2], x[1]))

        self.log(f"\nBest regions by latency:")
        for i, (country, avg_lat, min_lat, successful, total) in enumerate(
            regional_scores[:5]
        ):
            self.log(
                f"  {i+1}. {country}: {min_lat:.1f}ms min, {avg_lat:.1f}ms avg ({total} peers available)"
            )

        best_region = regional_scores[0][0]
        self.log(f"\nSelected region: {best_region}")
        return best_region

    def test_all_peers_in_region(self, country, peers):
        """Test all peers in the selected region simultaneously"""
        self.log(f"\nTesting all peers in {country} ({len(peers)} peers)...")
        self.log("=" * 60)

        results = []

        with ThreadPoolExecutor(max_workers=15) as executor:
            future_to_peer = {
                executor.submit(self.test_single_peer, peer, country): peer
                for peer in peers
            }

            for future in as_completed(future_to_peer, timeout=5):
                try:
                    result = future.result()
                    if result:
                        results.append(result)
                        if self.verbose:
                            self.log(
                                f"  {result['url']:<50} {result['latency']:6.1f}ms"
                            )
                    else:
                        if self.verbose:
                            peer = future_to_peer[future]
                            self.log(f"  {peer['address']:<50} FAILED")
                except:
                    continue

        return results


def main():
    parser = argparse.ArgumentParser(description="Find the best nearby Yggdrasil peers")
    parser.add_argument(
        "--no-verbose", action="store_true", help="Reduce output verbosity"
    )
    args = parser.parse_args()

    verbose = not args.no_verbose
    tester = SmartPeerTester(verbose=verbose)

    # Fetch peers from website
    peers_by_country = tester.fetch_peers()

    # Find best region
    best_region = tester.find_best_region(peers_by_country)
    if not best_region:
        print("No suitable regions found.")
        sys.exit(1)

    # Test all peers in best region
    region_results = tester.test_all_peers_in_region(
        best_region, peers_by_country[best_region]
    )

    if not region_results:
        print(f"No working peers found in {best_region}")
        sys.exit(1)

    # Sort by latency
    region_results.sort(key=lambda x: x["latency"])

    # Remove duplicate protocols (prefer fastest of each type)
    seen_protocols = set()
    filtered_results = []

    for result in region_results:
        protocol = result["protocol"]
        if protocol not in seen_protocols:
            filtered_results.append(result)
            seen_protocols.add(protocol)

        if len(filtered_results) >= 3:
            break

    # If we have less than 3 after protocol filtering, add more
    if len(filtered_results) < 3:
        for result in region_results:
            if result not in filtered_results:
                filtered_results.append(result)
                if len(filtered_results) >= 3:
                    break

    # Display results
    print(f"\n{'='*60}")
    print(f"TOP 3 RECOMMENDED PEERS IN {best_region.upper()}")
    print(f"{'='*60}")

    for i, result in enumerate(filtered_results, 1):
        print(f"{i}. {result['url']:<50} {result['latency']:6.1f}ms")

    # Generate config
    peer_urls = [result["url"] for result in filtered_results]
    config_line = f"  Peers: [{', '.join([f'\"{peer}\"' for peer in peer_urls])}]"

    print(f"\n{'='*60}")
    print("YGGDRASIL CONFIG (add to /etc/yggdrasil.conf):")
    print(f"{'='*60}")
    print(config_line)

    print(f"\nFound {len(filtered_results)} optimal peers in {best_region}")


if __name__ == "__main__":
    main()
