### Title
`DepositAllowlistExtension.beforeAddLiquidity` checks LP recipient (`owner`) instead of actual depositor (`sender`), allowing any address to bypass the deposit allowlist — (`metric-periphery/contracts/extensions/DepositAllowlistExtension.sol`)

---

### Summary

`DepositAllowlistExtension` is documented as gating `addLiquidity` **by depositor address**. Its `beforeAddLiquidity` hook silently drops the `sender` parameter and checks the `owner` parameter (LP position recipient) instead. Because `owner` is caller-supplied and independent of who actually provides the tokens, any address not on the allowlist can bypass the guard by naming an authorized address as `owner`.

---

### Finding Description

`MetricOmmPool.addLiquidity` calls the extension hook with `msg.sender` as `sender` and the user-supplied `owner` as the LP recipient: [1](#0-0) 

Inside `ExtensionCalling._beforeAddLiquidity`, the call is forwarded verbatim: [2](#0-1) 

`DepositAllowlistExtension.beforeAddLiquidity` receives both addresses but silently discards `sender` (first parameter is unnamed) and performs the allowlist lookup on `owner`: [3](#0-2) 

The mapping and NatSpec both name the guarded entity the *depositor* — the address that actually calls `addLiquidity` and pays tokens through the callback: [4](#0-3) 

Because `owner` is a free parameter chosen by the caller, an attacker who is **not** on the allowlist can pass any address that **is** on the allowlist as `owner`. The extension sees `allowedDepositor[pool][authorized_address] == true` and returns the success selector. The attacker then satisfies the token callback themselves, and the authorized address receives LP shares it never requested.

---

### Impact Explanation

The deposit allowlist is rendered completely ineffective:

1. **Access-control bypass** — any unprivileged address can add liquidity to a pool whose admin intended to restrict deposits (e.g., KYC/compliance, curated LP set, or protocol-only liquidity).
2. **Unauthorized pool-state mutation** — the attacker can place liquidity in arbitrary bins, altering the pool's bin distribution and affecting swap routing and fee accrual for existing LPs.
3. **Griefing of authorized owners** — the attacker forces an authorized address to hold an LP position it never created; while the owner can remove it, the position exposes them to pool risk in the interim and requires active management.

The invariant broken is: *only addresses explicitly permitted by the pool admin may add liquidity to a deposit-restricted pool*.

---

### Likelihood Explanation

Medium. Authorized owner addresses are typically public (known market makers, protocol multisigs, or addresses visible on-chain from prior deposits). An attacker needs only one such address to execute the bypass. No special privilege, flash loan, or oracle manipulation is required — a single `addLiquidity` call suffices.

---

### Recommendation

Name and check `sender` (the actual depositor) instead of `owner` (the LP recipient):

```solidity
// DepositAllowlistExtension.sol
function beforeAddLiquidity(address sender, address /*owner*/, uint80, LiquidityDelta calldata, bytes calldata)
    external
    view
    override
    returns (bytes4)
{
    if (!allowAllDepositors[msg.sender] && !allowedDepositor[msg.sender][sender]) {
        revert IMetricOmmPoolActions.NotAllowedToDeposit();
    }
    return IMetricOmmExtensions.beforeAddLiquidity.selector;
}
```

If the intent is to gate both the caller and the LP recipient, both should be checked explicitly.

---

### Proof of Concept

1. Pool is deployed with `DepositAllowlistExtension` as a `beforeAddLiquidity` hook.
2. Pool admin calls `setAllowedToDeposit(pool, alice, true)`. Bob is **not** on the allowlist.
3. Bob calls `pool.addLiquidity(alice, salt, deltas, callbackData, extensionData)`.
4. Pool calls `extension.beforeAddLiquidity(bob /*sender*/, alice /*owner*/, ...)`.
5. Extension evaluates `allowedDepositor[pool][alice]` → `true` → returns success selector. `bob` is never checked.
6. `LiquidityLib.addLiquidity` executes; the token callback fires against Bob's address; Bob pays the tokens.
7. Alice receives LP shares she never requested; Bob has successfully deposited into a pool he is not authorized to touch. [3](#0-2)

### Citations

**File:** metric-core/contracts/MetricOmmPool.sol (L191-195)
```text
    _beforeAddLiquidity(msg.sender, owner, salt, deltas, extensionData);
    (amount0Added, amount1Added) = LiquidityLib.addLiquidity(
      _liquidityContext(), owner, salt, deltas, callbackData, binTotals, _binStates, _binTotalShares, _positionBinShares
    );
    _afterAddLiquidity(msg.sender, owner, salt, deltas, amount0Added, amount1Added, extensionData);
```

**File:** metric-core/contracts/ExtensionCalling.sol (L88-99)
```text
  function _beforeAddLiquidity(
    address sender,
    address owner,
    uint80 salt,
    LiquidityDelta calldata deltas,
    bytes calldata extensionData
  ) internal {
    _callExtensionsInOrder(
      BEFORE_ADD_LIQUIDITY_ORDER,
      abi.encodeCall(IMetricOmmExtensions.beforeAddLiquidity, (sender, owner, salt, deltas, extensionData))
    );
  }
```

**File:** metric-periphery/contracts/extensions/DepositAllowlistExtension.sol (L10-14)
```text
/// @title DepositAllowlistExtension
/// @notice Gates `addLiquidity` by depositor address, per pool.
contract DepositAllowlistExtension is BaseMetricExtension, IDepositAllowlistExtension {
  mapping(address pool => mapping(address depositor => bool)) public allowedDepositor;
  mapping(address pool => bool) public allowAllDepositors;
```

**File:** metric-periphery/contracts/extensions/DepositAllowlistExtension.sol (L32-42)
```text
  function beforeAddLiquidity(address, address owner, uint80, LiquidityDelta calldata, bytes calldata)
    external
    view
    override
    returns (bytes4)
  {
    if (!allowAllDepositors[msg.sender] && !allowedDepositor[msg.sender][owner]) {
      revert IMetricOmmPoolActions.NotAllowedToDeposit();
    }
    return IMetricOmmExtensions.beforeAddLiquidity.selector;
  }
```
