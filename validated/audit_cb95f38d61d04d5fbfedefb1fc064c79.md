### Title
Unprotected `creditDeposit()` Allows Anyone to Force-Deposit DDA Tokens, Blocking Protocol Recovery - (File: `core/contracts/DirectDepositV1.sol`)

---

### Summary

`DirectDepositV1.creditDeposit()` has no access control. Any unprivileged caller can invoke it at any time to sweep all spot-product token balances held in a Direct Deposit Address (DDA) into the hardcoded `subaccount`. This directly mirrors the VaderPoolV2 `rescue()` bug class: a token-disposition function that should be gated is left open, allowing a third party to pre-empt the protocol's own recovery path (`withdrawFromDirectDepositV1`) via front-running.

---

### Finding Description

`DirectDepositV1` is a per-subaccount escrow contract deployed by `ContractOwner`. Its owner is `ContractOwner` (a multisig-controlled upgradeable contract). The owner-only `withdraw()` and `withdrawNative()` functions exist precisely so the multisig can recover tokens from a DDA when needed — for example, tokens sent to the wrong DDA, tokens that must be redirected, or tokens that arrived below the minimum deposit threshold and need manual handling.

`creditDeposit()` is the complementary sweep function: it iterates every spot product, reads the DDA's balance of each product token, approves the endpoint, and calls `endpoint.depositCollateralWithReferral(subaccount, ...)` to push the full balance into the hardcoded `subaccount`.

```solidity
// DirectDepositV1.sol line 83 — no modifier, callable by anyone
function creditDeposit() external {
    uint32[] memory productIds = spotEngine.getProductIds();
    for (uint256 i = 0; i < productIds.length; i++) {
        ...
        uint256 balance = token.balanceOf(address(this));
        if (balance != 0) {
            token.approve(address(endpoint), balance);
            endpoint.depositCollateralWithReferral(subaccount, productId, uint128(balance), "-1");
        }
    }
}
```

`ContractOwner.creditDepositV1()` (line 502) is also `external` with no modifier and simply delegates to `creditDeposit()`, providing a second permissionless entry point.

The owner-gated recovery path is:

```solidity
// ContractOwner.sol line 622 — onlyOwner
function withdrawFromDirectDepositV1(bytes32 subaccount, address token) external onlyOwner { ... }
```

Because `creditDeposit()` is unguarded, any observer (MEV searcher, griever) can front-run a pending `withdrawFromDirectDepositV1` transaction, atomically depositing the tokens into the subaccount before the multisig's recovery lands. Once deposited into the clearinghouse via the endpoint, the tokens are no longer in the DDA and `withdrawFromDirectDepositV1` reverts on the `require(postBalance > preBalance, "empty")` check.

---

### Impact Explanation

The concrete asset delta: tokens that the protocol (multisig) intended to recover from a DDA are instead irrevocably deposited into the associated `subaccount` in the clearinghouse. The multisig's recovery transaction reverts. The protocol loses its ability to redirect or reclaim those tokens.

Scenarios where this matters:
1. **Wrong-token deposit**: a user sends a spot-product token to the wrong DDA. The multisig attempts `withdrawFromDirectDepositV1` to return it. A front-runner calls `creditDeposit()` first, depositing the tokens into the wrong subaccount.
2. **Below-minimum-amount tokens**: tokens that fail `isValidDepositAmount` inside `depositCollateralWithReferral` are silently skipped (the function returns without reverting per the comment at Endpoint.sol line 138–141). These tokens remain in the DDA. The multisig may want to recover them, but a front-runner can keep calling `creditDeposit()` to prevent recovery indefinitely.
3. **Rebasing tokens**: any positive rebase accrued in the DDA is immediately claimable by anyone calling `creditDeposit()`, preventing the protocol from capturing or redirecting the rebase increment.

---

### Likelihood Explanation

High. `creditDeposit()` and `creditDepositV1()` are both `external` with no arguments beyond the subaccount (for the ContractOwner wrapper). Any on-chain observer watching the mempool for `withdrawFromDirectDepositV1` transactions can trivially front-run them. MEV infrastructure makes this a near-certain outcome whenever the multisig attempts a DDA recovery on a public mempool chain.

---

### Recommendation

Add an `onlyOwner` modifier to `DirectDepositV1.creditDeposit()`:

```solidity
function creditDeposit() external onlyOwner {
    ...
}
```

Since `ContractOwner` is the owner of every DDA it deploys, `creditDepositV1()` (which calls `creditDeposit()` via `ContractOwner`) will continue to work. Legitimate users who previously called `creditDeposit()` directly should instead call `ContractOwner.creditDepositV1()` — but that function should also be restricted (e.g., to the subaccount owner or a trusted keeper) to prevent the same front-running vector at the ContractOwner layer.

---

### Proof of Concept

1. Multisig detects that a DDA holds 1000 USDC that was sent to the wrong subaccount's DDA.
2. Multisig submits `ContractOwner.withdrawFromDirectDepositV1(wrongSubaccount, USDC_ADDRESS)`.
3. MEV bot observes the pending transaction in the mempool.
4. MEV bot front-runs with `DirectDepositV1(ddaAddress).creditDeposit()` (or equivalently `ContractOwner.creditDepositV1(wrongSubaccount)`).
5. `creditDeposit()` approves the endpoint for 1000 USDC and calls `endpoint.depositCollateralWithReferral(wrongSubaccount, usdcProductId, 1000e6, "-1")`.
6. The 1000 USDC is now in the clearinghouse credited to `wrongSubaccount`. The DDA balance is zero.
7. The multisig's `withdrawFromDirectDepositV1` transaction executes: `preBalance == postBalance`, so `require(postBalance > preBalance, "empty")` reverts.
8. Recovery is permanently blocked for this DDA until the multisig takes a different action (e.g., withdrawing from the clearinghouse subaccount, which may not be possible if the subaccount is not controlled by the multisig).

**Relevant code references:**

`DirectDepositV1.creditDeposit()` — no access control: [1](#0-0) 

`DirectDepositV1.withdraw()` — correctly gated `onlyOwner`: [2](#0-1) 

`ContractOwner.creditDepositV1()` — second permissionless entry point: [3](#0-2) 

`ContractOwner.withdrawFromDirectDepositV1()` — the owner-only recovery path that can be front-run: [4](#0-3)

### Citations

**File:** core/contracts/DirectDepositV1.sol (L83-101)
```text
    function creditDeposit() external {
        uint32[] memory productIds = spotEngine.getProductIds();
        for (uint256 i = 0; i < productIds.length; i++) {
            uint32 productId = productIds[i];
            address tokenAddr = spotEngine.getToken(productId);
            require(tokenAddr != address(0), "Invalid productId.");
            IIERC20Base token = IIERC20Base(tokenAddr);
            uint256 balance = token.balanceOf(address(this));
            if (balance != 0) {
                token.approve(address(endpoint), balance);
                endpoint.depositCollateralWithReferral(
                    subaccount,
                    productId,
                    uint128(balance),
                    "-1"
                );
            }
        }
    }
```

**File:** core/contracts/DirectDepositV1.sol (L103-106)
```text
    function withdraw(IIERC20Base token) external onlyOwner {
        uint256 balance = token.balanceOf(address(this));
        safeTransfer(token, msg.sender, balance);
    }
```

**File:** core/contracts/ContractOwner.sol (L502-508)
```text
    function creditDepositV1(bytes32 subaccount) external {
        address payable directDepositV1 = directDepositV1Address[subaccount];
        if (directDepositV1 == address(0)) {
            directDepositV1 = createDirectDepositV1(subaccount);
        }
        DirectDepositV1(directDepositV1).creditDeposit();
    }
```

**File:** core/contracts/ContractOwner.sol (L622-647)
```text
    function withdrawFromDirectDepositV1(bytes32 subaccount, address token)
        external
        onlyOwner
    {
        address payable directDepositV1 = directDepositV1Address[subaccount];
        require(directDepositV1 != address(0), "no dda");
        if (token == address(0)) {
            uint256 preBalance = address(this).balance;
            DirectDepositV1(directDepositV1).withdrawNative();
            uint256 postBalance = address(this).balance;
            require(postBalance > preBalance, "empty");
            (bool success, ) = msg.sender.call{value: postBalance - preBalance}(
                ""
            );
            require(success, "xfer");
        } else {
            uint256 preBalance = IERC20Base(token).balanceOf(address(this));
            DirectDepositV1(directDepositV1).withdraw(IIERC20Base(token));
            uint256 postBalance = IERC20Base(token).balanceOf(address(this));
            require(postBalance > preBalance, "empty");
            IERC20Base(token).safeTransfer(
                msg.sender,
                postBalance - preBalance
            );
        }
    }
```
