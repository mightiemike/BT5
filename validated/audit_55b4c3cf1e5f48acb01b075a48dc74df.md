Audit Report

## Title
`DepositAllowlistExtension.beforeAddLiquidity` checks `owner` instead of `sender`, allowing any address to bypass the deposit allowlist — (`metric-periphery/contracts/extensions/DepositAllowlistExtension.sol`)

## Summary
`DepositAllowlistExtension.beforeAddLiquidity` silently discards the `sender` parameter (the actual `msg.sender` of the `addLiquidity` call) and instead checks `owner` (the LP position recipient) against the allowlist. Because `MetricOmmPool.addLiquidity` explicitly supports an operator pattern where `msg.sender` and `owner` may differ, any unprivileged address can bypass the allowlist by supplying any already-allowlisted address as `owner`. The deposit allowlist is rendered completely ineffective.

## Finding Description
`MetricOmmPool.addLiquidity` accepts a caller-supplied `owner` argument independent of `msg.sender` and passes both to the extension hook:

```solidity
// MetricOmmPool.sol L191
_beforeAddLiquidity(msg.sender, owner, salt, deltas, extensionData);
```

The NatSpec for `addLiquidity` explicitly documents this operator pattern: *"msg.sender pays but need not equal owner (operator pattern)"*.

`ExtensionCalling._beforeAddLiquidity` correctly forwards both `sender` and `owner` to the extension:

```solidity
// ExtensionCalling.sol L95-98
abi.encodeCall(IMetricOmmExtensions.beforeAddLiquidity, (sender, owner, salt, deltas, extensionData))
```

However, `DepositAllowlistExtension.beforeAddLiquidity` drops `sender` (unnamed first parameter) and checks `owner` instead:

```solidity
// DepositAllowlistExtension.sol L32-41
function beforeAddLiquidity(address, address owner, uint80, LiquidityDelta calldata, bytes calldata)
    external view override returns (bytes4)
{
    if (!allowAllDepositors[msg.sender] && !allowedDepositor[msg.sender][owner]) {
        revert IMetricOmmPoolActions.NotAllowedToDeposit();
    }
    ...
}
```

The sibling `SwapAllowlistExtension.beforeSwap` correctly checks `sender`:

```solidity
// SwapAllowlistExtension.sol L37
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
```

**Exploit path:**
1. Pool admin deploys pool with `DepositAllowlistExtension` and calls `setAllowedToDeposit(pool, alice, true)`.
2. `bob` (not allowlisted) calls `pool.addLiquidity(alice, salt, deltas, callbackData, "")` directly.
3. The extension receives `sender=bob`, `owner=alice`; it evaluates `allowedDepositor[pool][alice]` → `true` → no revert.
4. `bob`'s tokens are pulled via the modify-liquidity callback; the LP position is minted to `alice`.
5. The check that should have blocked `bob` (`allowedDepositor[pool][bob]`) is never evaluated.

No privileged access, flash loans, or special setup is required. The pool can be called directly, bypassing `MetricOmmPoolLiquidityAdder` entirely.

## Impact Explanation
The deposit allowlist is completely ineffective. Any address can inject liquidity into a restricted pool (e.g., KYC/institutional-only pools) by setting `owner` to any known allowlisted address. The allowlisted address receives an unsolicited LP position, altering their exposure. An attacker can also manipulate the pool's bin distribution (concentrate liquidity in specific bins) without authorization, potentially harming existing LPs by shifting the effective price range or diluting fee share. This constitutes broken core pool functionality causing loss of funds and violation of the access-control invariant the extension is designed to enforce.

## Likelihood Explanation
The trigger requires only a standard `addLiquidity` call with `owner` set to any known allowlisted address. Allowlisted addresses are discoverable on-chain (e.g., the pool admin, existing LPs). No privileged access, special tokens, flash loans, or malicious setup is needed. The attack is repeatable at any time. Likelihood is **High**.

## Recommendation
Replace the `owner` check with a `sender` check, mirroring `SwapAllowlistExtension`:

```solidity
function beforeAddLiquidity(address sender, address, uint80, LiquidityDelta calldata, bytes calldata)
    external view override returns (bytes4)
{
    if (!allowAllDepositors[msg.sender] && !allowedDepositor[msg.sender][sender]) {
        revert IMetricOmmPoolActions.NotAllowedToDeposit();
    }
    return IMetricOmmExtensions.beforeAddLiquidity.selector;
}
```

Pool admins should then allowlist the actual depositor addresses (e.g., the router/adder contract or individual LP addresses), not position-owner addresses.

## Proof of Concept
1. Deploy a pool with `DepositAllowlistExtension` configured in `beforeAddLiquidity`.
2. Pool admin calls `setAllowedToDeposit(pool, alice, true)` — only `alice` is authorized.
3. `bob` (not allowlisted) calls `pool.addLiquidity(alice, salt, deltas, callbackData, "")` directly.
4. Extension evaluates `allowedDepositor[pool][alice]` → `true` → no revert.
5. `bob`'s tokens are pulled via callback; LP position minted to `alice`.
6. `bob` has successfully deposited into a restricted pool without being allowlisted.

Foundry test skeleton:
```solidity
function test_depositAllowlist_bypass() public {
    // alice is allowlisted, bob is not
    vm.prank(poolAdmin);
    depositAllowlist.setAllowedToDeposit(address(pool), alice, true);

    // bob calls addLiquidity with owner=alice
    vm.prank(bob);
    pool.addLiquidity(alice, salt, deltas, callbackData, "");
    // succeeds — bob bypassed the allowlist
}
```