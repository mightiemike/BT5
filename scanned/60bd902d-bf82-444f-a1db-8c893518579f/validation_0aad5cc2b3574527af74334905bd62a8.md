The underlying vulnerability is real, but the question's "same transaction" framing is mechanically impossible and must be corrected. Here is the full analysis:

---

### Title
Unguarded `refundETH()` allows any caller to drain ETH left on `MetricOmmSimpleRouter` between transactions — (`metric-periphery/contracts/base/PeripheryPayments.sol`)

---

### Summary

`refundETH()` unconditionally transfers the router's entire native ETH balance to `msg.sender` with no access control. Because `multicall` is `payable`, a user can send ETH to the router in one transaction. If that user omits a `refundETH()` call at the end of their multicall batch, any ETH not consumed by the swap persists on the contract. A subsequent unprivileged caller can then invoke `refundETH()` in a separate transaction and receive all of it.

---

### Finding Description

`PeripheryPayments.refundETH()` is:

```solidity
function refundETH() external payable override {
    uint256 balance = address(this).balance;
    if (balance > 0) {
        _transferETH(msg.sender, balance);   // no caller check
    }
}
``` [1](#0-0) 

There is no check that `msg.sender` is the address that originally deposited the ETH. The function simply sends `address(this).balance` to whoever calls it.

ETH enters the contract legitimately via `multicall`, which is `public payable`:

```solidity
function multicall(bytes[] calldata data) public payable override returns (bytes[] memory results) {
``` [2](#0-1) 

The `receive()` fallback blocks direct ETH sends (only WETH is allowed), but this does not prevent ETH from entering via payable function calls:

```solidity
receive() external payable {
    if (msg.sender != WETH) revert NotWETH();
}
``` [3](#0-2) 

Inside `pay()`, when `token == WETH`, only the exact amount needed for the swap is deposited into WETH; any excess native ETH remains on the contract:

```solidity
if (nativeBalance >= value) {
    IWETH9(WETH).deposit{value: value}();
    IERC20(WETH).safeTransfer(recipient, value);
}
``` [4](#0-3) 

---

### Correction to the Question's Framing

The question states the attacker acts "in the same transaction." This is mechanically impossible: `multicall` iterates over `data` with `delegatecall`, so every sub-call shares the original `msg.sender`. An attacker cannot inject a `refundETH()` call into a victim's multicall batch. The real attack is **cross-transaction**:

1. User A calls `multicall{value: 1 ETH}([exactInputSingle_calldata])` — swap consumes 0.5 ETH; 0.5 ETH remains on the router.
2. Attacker calls `refundETH()` in a separate transaction.
3. Attacker receives 0.5 ETH; user A's ETH is unrecoverable.

---

### Impact Explanation

Direct loss of user ETH principal. Any ETH deposited via a payable multicall that is not consumed by the swap and not reclaimed within the same multicall batch is permanently accessible to any external caller. The loss is bounded only by how much ETH the victim sent.

---

### Likelihood Explanation

Medium. Users following the standard Uniswap-style pattern are expected to append `refundETH()` as the last call in their multicall. Users who omit it — whether through UI bugs, direct contract interaction, or integrator error — leave ETH exposed. MEV bots routinely monitor for stranded ETH on router contracts.

---

### Recommendation

Restrict `refundETH()` so it can only be called as part of a `multicall` batch (e.g., track the depositor in transient storage at `multicall` entry and require `msg.sender == depositor` inside `refundETH()`), or remove the standalone `refundETH()` entrypoint and only allow ETH refunds within the multicall context where the original depositor is the `msg.sender`.

---

### Proof of Concept

```solidity
// SPDX-License-Identifier: MIT
pragma solidity ^0.8.35;

import "forge-std/Test.sol";
import {MetricOmmSimpleRouter} from "metric-periphery/contracts/MetricOmmSimpleRouter.sol";

contract RefundETHDrainTest is Test {
    MetricOmmSimpleRouter router;
    address userA  = address(0xA);
    address attacker = address(0xB);

    function setUp() public {
        // deploy with stub WETH and factory (addresses irrelevant for this path)
        router = new MetricOmmSimpleRouter(address(weth), address(factory));
        vm.deal(userA, 1 ether);
    }

    function test_attackerDrainsUserETH() public {
        // User A sends 1 ETH via multicall but only uses 0.5 ETH in the swap;
        // they forget to append refundETH() to the batch.
        // Simulate residual ETH on the router after the swap settles.
        vm.prank(userA);
        // Force 0.5 ETH to remain on the router (swap stub consumes 0.5 ETH).
        vm.deal(address(router), 0.5 ether);

        uint256 attackerBefore = attacker.balance;

        vm.prank(attacker);
        router.refundETH();

        // Attacker received user A's ETH
        assertEq(attacker.balance, attackerBefore + 0.5 ether);
        // Router is drained
        assertEq(address(router).balance, 0);
    }
}
```

### Citations

**File:** metric-periphery/contracts/base/PeripheryPayments.sol (L32-34)
```text
  receive() external payable {
    if (msg.sender != WETH) revert NotWETH();
  }
```

**File:** metric-periphery/contracts/base/PeripheryPayments.sol (L58-63)
```text
  function refundETH() external payable override {
    uint256 balance = address(this).balance;
    if (balance > 0) {
      _transferETH(msg.sender, balance);
    }
  }
```

**File:** metric-periphery/contracts/base/PeripheryPayments.sol (L75-77)
```text
      if (nativeBalance >= value) {
        IWETH9(WETH).deposit{value: value}();
        IERC20(WETH).safeTransfer(recipient, value);
```

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L39-44)
```text
  function multicall(bytes[] calldata data) public payable override returns (bytes[] memory results) {
    results = new bytes[](data.length);
    for (uint256 i = 0; i < data.length; i++) {
      results[i] = Address.functionDelegateCall(address(this), data[i]);
    }
  }
```
