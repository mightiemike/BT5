### Title
Unguarded `creditDeposit()` Allows Anyone to Permanently Credit Accidentally-Sent Tokens to a Fixed Subaccount, Front-Running Owner Recovery — (`File: core/contracts/DirectDepositV1.sol`)

---

### Summary

`DirectDepositV1.creditDeposit()` has no access control. It reads the contract's live `balanceOf` for every registered product token and deposits the full balance to a **fixed, immutable `subaccount`** set at construction. Because the owner's only recovery path (`withdraw()`) is a plain `onlyOwner` call with no atomicity guarantee, any attacker can front-run it by calling `creditDeposit()` first, permanently crediting the tokens to the fixed subaccount and making them irrecoverable by the original sender.

---

### Finding Description

`DirectDepositV1` is deployed with a fixed `subaccount` (line 32) that never changes after construction. The `creditDeposit()` function (line 83) is `external` with no access-control modifier:

```solidity
function creditDeposit() external {                          // ← no modifier
    uint32[] memory productIds = spotEngine.getProductIds();
    for (uint256 i = 0; i < productIds.length; i++) {
        ...
        uint256 balance = token.balanceOf(address(this));   // reads live balance
        if (balance != 0) {
            token.approve(address(endpoint), balance);
            endpoint.depositCollateralWithReferral(
                subaccount,                                  // always the fixed address
                productId,
                uint128(balance),
                "-1"
            );
        }
    }
}
``` [1](#0-0) 

The owner's only recovery mechanism is:

```solidity
function withdraw(IIERC20Base token) external onlyOwner {
    uint256 balance = token.balanceOf(address(this));
    safeTransfer(token, msg.sender, balance);
}
``` [2](#0-1) 

These two functions operate on the same `balanceOf` state with no mutex or ordering guarantee. Any call to `creditDeposit()` by a third party atomically drains the balance into the fixed `subaccount` before the owner's `withdraw()` transaction can execute.

---

### Impact Explanation

A user who accidentally sends ERC-20 tokens to a `DirectDepositV1` address (a realistic mistake given the contract is a deposit address) loses those tokens permanently if an attacker (or MEV bot) calls `creditDeposit()` before the owner calls `withdraw()`. The tokens are credited to the fixed `subaccount` in `SpotEngine`'s internal balance ledger — a subaccount the original sender does not control. There is no subsequent path to recover them without a protocol upgrade or owner-level intervention on the receiving subaccount. This is a direct asset loss for the user and a cross-contract desynchronization: the `SpotEngine` internal balance for the fixed `subaccount` grows without any intentional deposit action by that subaccount's owner. [3](#0-2) 

---

### Likelihood Explanation

- `DirectDepositV1` is a publicly known deposit address, making accidental token transfers realistic (wrong-address mistakes are common in DeFi).
- `creditDeposit()` is callable by any EOA or contract with zero preconditions.
- MEV bots routinely monitor mempools for profitable or griefing opportunities; a pending `withdraw()` transaction is trivially front-runnable.
- No special privilege, governance action, or key compromise is required.

---

### Recommendation

Add an `onlyOwner` modifier to `creditDeposit()`, or alternatively restrict it to a whitelist of callers. This mirrors the recommendation in the original report: ensure that the deposit-crediting path cannot be triggered by an unprivileged caller when the contract holds tokens that may not belong to the intended `subaccount`.

```solidity
function creditDeposit() external onlyOwner {
    ...
}
``` [4](#0-3) 

---

### Proof of Concept

1. `DirectDepositV1` is deployed for `subaccount = 0xProtocolAccount...`.
2. User mistakenly sends 1000 USDC directly to the `DirectDepositV1` address.
3. User's owner calls `withdraw(USDC)` to recover the tokens.
4. Attacker observes the pending `withdraw()` in the mempool and submits `creditDeposit()` with higher gas.
5. `creditDeposit()` executes first: reads `balanceOf(DirectDepositV1) = 1000 USDC`, approves `Endpoint`, calls `depositCollateralWithReferral(0xProtocolAccount..., productId, 1000e6, "-1")`.
6. `SpotEngine.updateBalance` credits 1000 USDC to `0xProtocolAccount...`.
7. User's `withdraw()` executes but `balanceOf = 0`; nothing is transferred.
8. User's 1000 USDC is permanently credited to `0xProtocolAccount...` with no recovery path for the user. [1](#0-0) [2](#0-1)

### Citations

**File:** core/contracts/DirectDepositV1.sol (L29-32)
```text
contract DirectDepositV1 is Ownable {
    IIEndpoint internal endpoint;
    IISpotEngine internal spotEngine;
    bytes32 internal subaccount;
```

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
