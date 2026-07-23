Audit Report

## Title
Stranded Native ETH on Router Consumed by Subsequent WETH Payer — (`metric-periphery/contracts/base/PeripheryPayments.sol`)

## Summary

`PeripheryPayments.pay()` reads `address(this).balance` — the router's entire native ETH balance — as the payment source when `token == WETH` and `payer != address(this)`. Any ETH left on the router from a prior `payable` call is silently consumed to partially satisfy a later user's WETH obligation, causing the original depositor to permanently lose those funds.

## Finding Description

In `pay()`, when `token == WETH` and `payer != address(this)`, the function reads `address(this).balance` at line 74 and uses it as a payment source: [1](#0-0) 

If `0 < nativeBalance < value`, the `else if` branch at line 78 deposits the entire router ETH balance as WETH, transfers it to the pool, then pulls only the remainder from `payer`. This means any ETH sitting on the router from any prior source is consumed as part of the current user's payment.

The `receive()` guard only blocks plain ETH sends: [2](#0-1) 

It does **not** block `msg.value` attached to `payable` function calls. Both `exactInputSingle` and `multicall` are `payable`: [3](#0-2) [4](#0-3) 

**Stranding paths:**
1. User A calls `exactInputSingle` with `tokenIn = USDC` and `msg.value = 1 ETH`. The ETH is never consumed (non-WETH path, line 86) and remains on the router.
2. User A calls `exactInputSingle` with `tokenIn = WETH`, `amountIn = 1 ETH`, `msg.value = 2 ETH`. The `nativeBalance >= value` branch at line 75 deposits exactly `value` (1 ETH), leaving 1 ETH stranded.

**Exploitation path:**
1. User A strands 1 ETH on the router via either path above.
2. User B calls `exactInputSingle` with `tokenIn = WETH`, `amountIn = 10 ETH`, `msg.value = 0`.
3. In the swap callback, `_justPayCallback` calls `pay(WETH, UserB, pool, 10e18)`.
4. `nativeBalance = 1e18` (User A's stranded ETH), `value = 10e18`.
5. The `else if (nativeBalance > 0)` branch fires: router deposits 1 ETH as WETH → sends to pool, then pulls only 9 WETH from User B.
6. User A's 1 ETH is gone. User B's wallet is debited only 9 WETH instead of 10 WETH.

`refundETH()` is not a sufficient guard — it is a separate call that must be included in the same multicall sequence, and any ETH not refunded before a subsequent WETH swap is consumed. This is a race condition, not a guarantee. [5](#0-4) 

## Impact Explanation

Direct loss of user principal. Any ETH stranded on the router — even accidentally, e.g., wrong `msg.value` on a non-WETH swap — is permanently transferred to the pool on behalf of the next WETH payer. The original depositor cannot recover it once consumed. This meets the Sherlock High/Critical threshold for direct loss of user funds with no privileged access required.

## Likelihood Explanation

Stranding ETH is easy and realistic: sending `msg.value > 0` with a non-WETH `exactInputSingle`, or sending excess ETH with a WETH swap, or any multicall sequence where one leg sends ETH and a later leg does not consume it all. No privileged access is required. The consuming transaction is any ordinary WETH-input swap by any user.

## Recommendation

In `pay()`, when `token == WETH` and `payer != address(this)`, only use the ETH sent in the **current** transaction as the native source, not `address(this).balance`. Track consumed `msg.value` per-call (e.g., via a transient storage slot or a parameter) so residual ETH from prior calls is never touched. Alternatively, require that the WETH path always pulls entirely from `payer` via `safeTransferFrom` unless `payer == address(this)`, and handle the ETH→WETH wrapping only for the current call's `msg.value`.

## Proof of Concept

```solidity
// Foundry integration test sketch
function test_strandedEthConsumedByWethPayer() public {
    // 1. User A calls exactInputSingle with tokenIn=USDC, msg.value=1 ether
    //    → 1 ETH stranded on router (non-WETH path never touches it)
    vm.prank(userA);
    router.exactInputSingle{value: 1 ether}(ExactInputSingleParams({
        tokenIn: USDC, tokenOut: WETH, pool: usdcWethPool,
        amountIn: 1000e6, amountOutMinimum: 0, ...
    }));
    assertEq(address(router).balance, 1 ether);

    // 2. User B calls exactInputSingle with tokenIn=WETH, amountIn=10 ether, msg.value=0
    uint256 bBalanceBefore = IERC20(WETH).balanceOf(userB);
    vm.prank(userB);
    router.exactInputSingle(ExactInputSingleParams({
        tokenIn: WETH, tokenOut: USDC, pool: wethUsdcPool,
        amountIn: 10 ether, amountOutMinimum: 0, ...
    }));

    // 3. User B only spent 9 WETH from wallet; router consumed User A's 1 ETH
    assertEq(bBalanceBefore - IERC20(WETH).balanceOf(userB), 9 ether);
    assertEq(address(router).balance, 0);
    // User A's 1 ETH is gone — lost to User B's swap
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

**File:** metric-periphery/contracts/base/PeripheryPayments.sol (L73-84)
```text
    } else if (token == WETH) {
      uint256 nativeBalance = address(this).balance;
      if (nativeBalance >= value) {
        IWETH9(WETH).deposit{value: value}();
        IERC20(WETH).safeTransfer(recipient, value);
      } else if (nativeBalance > 0) {
        IWETH9(WETH).deposit{value: nativeBalance}();
        IERC20(WETH).safeTransfer(recipient, nativeBalance);
        IERC20(WETH).safeTransferFrom(payer, recipient, value - nativeBalance);
      } else {
        IERC20(WETH).safeTransferFrom(payer, recipient, value);
      }
```

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L39-39)
```text
  function multicall(bytes[] calldata data) public payable override returns (bytes[] memory results) {
```

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L67-67)
```text
  function exactInputSingle(ExactInputSingleParams calldata params) external payable returns (uint256 amountOut) {
```
