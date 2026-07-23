Audit Report

## Title
Unguarded `refundETH()` allows any caller to drain stranded ETH from `MetricOmmSimpleRouter` across transactions — (`metric-periphery/contracts/base/PeripheryPayments.sol`)

## Summary

`PeripheryPayments.refundETH()` transfers the router's entire native ETH balance to `msg.sender` with no access control. ETH sent via a `payable` `multicall` batch that is not fully consumed by the swap's `pay()` call remains on the contract after the transaction settles. Any subsequent unprivileged caller can invoke `refundETH()` in a separate transaction and receive all of it, causing direct loss of the original depositor's ETH principal.

## Finding Description

`refundETH()` contains no caller check: [1](#0-0) 

`multicall` is `public payable`, so ETH can legitimately enter the router: [2](#0-1) 

The `receive()` fallback only blocks *direct* ETH sends; it does not prevent ETH from entering via payable function calls: [3](#0-2) 

Inside `pay()`, when `token == WETH` and `nativeBalance >= value`, only the exact swap-required amount is wrapped and forwarded; any excess native ETH is left on the contract: [4](#0-3) 

**Cross-transaction attack path:**
1. User A calls `multicall{value: 1 ETH}([exactInputSingle_calldata])`. The swap consumes 0.5 ETH via `pay()`; 0.5 ETH remains on the router after the transaction.
2. Attacker calls `router.refundETH()` in a separate transaction.
3. `refundETH()` sends `address(this).balance` (0.5 ETH) to the attacker. User A's ETH is unrecoverable.

The "same transaction" framing in the original question is mechanically impossible: `multicall` uses `delegatecall`, so every sub-call inherits the original `msg.sender`. An attacker cannot inject a `refundETH()` call into a victim's multicall batch. The real exploit is strictly cross-transaction.

## Impact Explanation

Direct loss of user ETH principal. Any ETH deposited via a payable multicall that is not fully consumed by the swap and not reclaimed within the same multicall batch is permanently accessible to any external caller. Loss is bounded only by the victim's deposited amount. This meets the "direct loss of user principal" impact gate at Medium-to-High severity depending on deposited value.

## Likelihood Explanation

Medium. Users following the standard Uniswap-style pattern are expected to append `refundETH()` as the last call in their multicall batch. Users who omit it — through UI bugs, direct contract interaction, or integrator error — leave ETH exposed. MEV bots routinely monitor for stranded ETH on router contracts and can extract it atomically in the next block.

## Recommendation

Restrict `refundETH()` so it can only refund the original depositor. One approach: at `multicall` entry, write `msg.sender` into transient storage as the authorized refund recipient, and inside `refundETH()` require `msg.sender == transient_depositor`. Alternatively, remove the standalone `refundETH()` entrypoint entirely and only allow ETH refunds within the multicall context where the original depositor is already `msg.sender`.

## Proof of Concept

```solidity
// SPDX-License-Identifier: MIT
pragma solidity ^0.8.35;

import "forge-std/Test.sol";
import {MetricOmmSimpleRouter} from "metric-periphery/contracts/MetricOmmSimpleRouter.sol";

contract RefundETHDrainTest is Test {
    MetricOmmSimpleRouter router;
    address userA    = address(0xA);
    address attacker = address(0xB);

    function setUp() public {
        router = new MetricOmmSimpleRouter(address(weth), address(factory));
        vm.deal(userA, 1 ether);
    }

    function test_attackerDrainsUserETH() public {
        // Simulate: userA sent 1 ETH via multicall, swap consumed 0.5 ETH,
        // 0.5 ETH remains on the router (userA forgot to append refundETH()).
        vm.deal(address(router), 0.5 ether);

        uint256 attackerBefore = attacker.balance;

        vm.prank(attacker);
        router.refundETH();   // no access control — succeeds

        assertEq(attacker.balance, attackerBefore + 0.5 ether);
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
