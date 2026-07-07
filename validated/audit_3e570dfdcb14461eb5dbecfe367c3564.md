### Title
Unchecked `transferFrom` Return Value in `replaceUsdcEWithUsdc` Enables usdcE Theft from DirectDepositV1 Accounts - (File: `core/contracts/ContractOwner.sol`)

---

### Summary

`ContractOwner.replaceUsdcEWithUsdc` calls `IERC20Base(usdc).transferFrom(...)` without checking its return value. If the USDC token at the hardcoded Ink mainnet address returns `false` on failure rather than reverting, an unprivileged caller can drain usdcE from any `DirectDepositV1` account without depositing any USDC.

---

### Finding Description

`replaceUsdcEWithUsdc` is a permissionless migration helper (guarded only by a `block.chainid == 57073` check) that is intended to atomically swap usdcE held in a `DirectDepositV1` (DDA) for USDC: the caller deposits USDC into the DDA, the DDA's usdcE is withdrawn to `ContractOwner`, and then forwarded to the caller.

The critical flaw is at line 616:

```solidity
IERC20Base(usdc).transferFrom(msg.sender, directDepositV1, balance);
```

The return value of this call is silently discarded. If the USDC token returns `false` on a failed transfer (e.g., caller has insufficient balance) instead of reverting, execution continues unconditionally to:

```solidity
DirectDepositV1(directDepositV1).withdraw(IIERC20Base(usdcE)); // pulls usdcE → ContractOwner
IERC20Base(usdcE).safeTransfer(msg.sender, balance);           // sends usdcE → attacker
```

The `ContractOwner` contract already imports and enables `ERC20Helper` via `using ERC20Helper for IERC20Base`, which provides a `safeTransferFrom` wrapper that checks the return value and reverts on failure. That safe wrapper is used elsewhere in the same contract (e.g., line 618 uses `safeTransfer`), but was not applied to the inbound USDC pull at line 616. [1](#0-0) 

The `ERC20Helper.safeTransferFrom` that should have been used: [2](#0-1) 

The `using ERC20Helper for IERC20Base` declaration that makes it available in `ContractOwner`: [3](#0-2) 

---

### Impact Explanation

An attacker who calls `replaceUsdcEWithUsdc(subaccount)` for any DDA that holds a non-zero usdcE balance, while holding zero USDC (or any amount less than `balance`), will:

1. Cause `usdc.transferFrom` to fail silently (return `false`).
2. Trigger `DDA.withdraw(usdcE)`, which transfers the DDA's entire usdcE balance to `ContractOwner`.
3. Receive that usdcE via `usdcE.safeTransfer(msg.sender, balance)`.

The attacker receives real usdcE tokens without depositing any USDC. The DDA's usdcE balance — which represents collateral belonging to the subaccount owner — is stolen. The broken invariant is: *usdcE must only leave the DDA after an equivalent USDC deposit has been confirmed*. [4](#0-3) 

---

### Likelihood Explanation

The function is callable by any unprivileged address on Ink mainnet (chain 57073) with no further access control. The USDC token at `0x2D270e6886d130D724215A266106e6832161EAEd` on Ink is a bridged/wrapped deployment whose exact revert-vs-return-false behavior on insufficient balance is not guaranteed by the contract code. Bridged ERC20 variants on newer chains have historically shipped with non-reverting transfer semantics. Even if the current deployment reverts, a proxy upgrade to a non-reverting version would immediately activate this path. Any DDA with a non-zero usdcE balance is a target.

---

### Recommendation

Replace the raw `transferFrom` call at line 616 with the `safeTransferFrom` wrapper already available via `ERC20Helper`:

```solidity
// Before (vulnerable):
IERC20Base(usdc).transferFrom(msg.sender, directDepositV1, balance);

// After (safe):
IERC20Base(usdc).safeTransferFrom(msg.sender, directDepositV1, balance);
```

`safeTransferFrom` in `ERC20Helper` uses a low-level `call` and requires `success && (data.length == 0 || abi.decode(data, (bool)))`, which correctly handles both reverting and return-false ERC20 semantics. [2](#0-1) 

---

### Proof of Concept

**Setup (Ink mainnet, chain 57073):**
- A DDA exists for `subaccount` with `usdcE.balanceOf(dda) = 1000e6`.
- Attacker holds 0 USDC.

**Steps:**
1. Attacker calls `ContractOwner.replaceUsdcEWithUsdc(subaccount)`.
2. `balance = usdcE.balanceOf(dda)` → `1000e6`.
3. `usdc.transferFrom(attacker, dda, 1000e6)` → returns `false` (attacker has no USDC); no revert.
4. `dda.withdraw(usdcE)` → transfers `1000e6` usdcE from DDA to `ContractOwner`.
5. `usdcE.safeTransfer(attacker, 1000e6)` → transfers `1000e6` usdcE from `ContractOwner` to attacker.

**Result:** Attacker receives `1000e6` usdcE. The subaccount owner's DDA is drained. No USDC was deposited. [1](#0-0)

### Citations

**File:** core/contracts/ContractOwner.sol (L24-24)
```text
    using ERC20Helper for IERC20Base;
```

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
