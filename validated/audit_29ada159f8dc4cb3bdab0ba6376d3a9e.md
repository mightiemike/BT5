### Title
Unchecked `transferFrom` Return Value Enables usdcE Drain Without USDC Payment — (`File: core/contracts/ContractOwner.sol`)

---

### Summary

In `ContractOwner.replaceUsdcEWithUsdc`, the `transferFrom` call used to pull USDC from the caller ignores its boolean return value. If the call fails silently (returns `false` instead of reverting), the function continues and transfers usdcE out of the DirectDepositV1 account to the caller — with no USDC ever received in exchange.

---

### Finding Description

`replaceUsdcEWithUsdc` is an `external` function with no caller access control beyond a chain ID check. Its intended flow is:

1. Pull `balance` USDC from `msg.sender` into the DDA (`transferFrom`)
2. Withdraw usdcE from the DDA to `ContractOwner` (`withdraw`)
3. Forward usdcE from `ContractOwner` to `msg.sender` (`safeTransfer`)

At step 1, the raw `IERC20Base.transfer`-family call is used without checking the return value:

```solidity
IERC20Base(usdc).transferFrom(msg.sender, directDepositV1, balance);  // line 616 — return ignored
DirectDepositV1(directDepositV1).withdraw(IIERC20Base(usdcE));        // line 617
IERC20Base(usdcE).safeTransfer(msg.sender, balance);                  // line 618 — safe
``` [1](#0-0) 

The same file already imports and uses `ERC20Helper.safeTransferFrom` elsewhere, and `ERC20Helper` provides exactly this wrapper: [2](#0-1) 

The inconsistency is stark: line 618 correctly uses `safeTransfer` for the outbound usdcE leg, but line 616 uses a bare `transferFrom` for the inbound USDC leg.

---

### Impact Explanation

If USDC's `transferFrom` returns `false` rather than reverting (e.g., caller has zero allowance, or a non-standard ERC-20 is substituted at that address in a future upgrade), execution continues uninterrupted. The DDA's entire usdcE balance is then transferred to the caller for free. The DDA loses its usdcE holdings; the protocol's accounting for that subaccount is corrupted because the intended USDC replacement never occurred.

**Corrupted asset delta**: usdcE balance of `directDepositV1` → drained to attacker; USDC balance of `directDepositV1` → unchanged (zero increase).

---

### Likelihood Explanation

The function is `external` with no `onlyOwner` or similar guard — any unprivileged caller on chain ID 57073 (Ink) can invoke it for any subaccount that has a deployed DDA with a non-zero usdcE balance. The attacker only needs to call the function without having approved USDC. Whether USDC on that chain returns `false` or reverts on failure determines exploitability; if it reverts, the bug is latent but still a correctness defect that violates the safe-transfer pattern used everywhere else in the codebase.

---

### Recommendation

Replace the bare `transferFrom` with `safeTransferFrom` from the already-imported `ERC20Helper` library, consistent with how every other transfer in the contract is handled:

```solidity
// Before (line 616)
IERC20Base(usdc).transferFrom(msg.sender, directDepositV1, balance);

// After
IERC20Base(usdc).safeTransferFrom(msg.sender, directDepositV1, balance);
``` [3](#0-2) 

---

### Proof of Concept

1. A DDA exists for some `subaccount` on chain 57073 with `usdcE.balanceOf(dda) = N > 0`.
2. Attacker calls `ContractOwner.replaceUsdcEWithUsdc(subaccount)` with zero USDC allowance granted to `ContractOwner`.
3. `IERC20Base(usdc).transferFrom(attacker, dda, N)` returns `false` (no revert).
4. `DirectDepositV1(dda).withdraw(usdcE)` transfers N usdcE to `ContractOwner`.
5. `IERC20Base(usdcE).safeTransfer(attacker, N)` transfers N usdcE to the attacker.
6. Attacker receives N usdcE; DDA receives 0 USDC. Net: attacker drains the DDA's usdcE balance at zero cost. [4](#0-3)

### Citations

**File:** core/contracts/ContractOwner.sol (L608-620)
```text
    function replaceUsdcEWithUsdc(bytes32 subaccount) external {
        require(block.chainid == 57073, ERR_UNAUTHORIZED);
        address payable directDepositV1 = directDepositV1Address[subaccount];
        require(directDepositV1 != address(0), "no dda");
        address usdcE = 0xF1815bd50389c46847f0Bda824eC8da914045D14;
        address usdc = 0x2D270e6886d130D724215A266106e6832161EAEd;
        uint256 balance = IERC20Base(usdcE).balanceOf(directDepositV1);
        if (balance > 0) {
            IERC20Base(usdc).transferFrom(msg.sender, directDepositV1, balance);
            DirectDepositV1(directDepositV1).withdraw(IIERC20Base(usdcE));
            IERC20Base(usdcE).safeTransfer(msg.sender, balance);
        }
    }
```

**File:** core/contracts/libraries/ERC20Helper.sol (L23-42)
```text
    function safeTransferFrom(
        IERC20Base self,
        address from,
        address to,
        uint256 amount
    ) internal {
        (bool success, bytes memory data) = address(self).call(
            abi.encodeWithSelector(
                IERC20Base.transferFrom.selector,
                from,
                to,
                amount
            )
        );

        require(
            success && (data.length == 0 || abi.decode(data, (bool))),
            ERR_TRANSFER_FAILED
        );
    }
```
