### Title
Unchecked `transferFrom` Return Value Enables usdcE Drain Without Providing USDC - (File: `core/contracts/ContractOwner.sol`)

---

### Summary

`ContractOwner.replaceUsdcEWithUsdc()` calls `IERC20Base(usdc).transferFrom(msg.sender, directDepositV1, balance)` without checking the return value. If the call returns `false` instead of reverting, execution continues, and the caller receives usdcE tokens from the DDA without having provided any USDC.

---

### Finding Description

`replaceUsdcEWithUsdc` is an externally callable function (no `onlyOwner` modifier) restricted only by a chain ID check (`block.chainid == 57073`). Its intended logic is a token swap: the caller provides USDC, the DDA's usdcE is withdrawn to `ContractOwner`, and then forwarded to the caller.

The critical step — pulling USDC from the caller — uses a raw `transferFrom` call whose boolean return value is silently discarded:

```solidity
IERC20Base(usdc).transferFrom(msg.sender, directDepositV1, balance); // unchecked
DirectDepositV1(directDepositV1).withdraw(IIERC20Base(usdcE));
IERC20Base(usdcE).safeTransfer(msg.sender, balance);
```

If `transferFrom` returns `false` (e.g., insufficient allowance, token-specific failure mode), the function does not revert. It proceeds to:
1. Call `withdraw(usdcE)` on the DDA — which transfers usdcE to `ContractOwner` (valid, since `ContractOwner` is the DDA owner).
2. Call `safeTransfer(msg.sender, balance)` — which transfers usdcE to the attacker.

The attacker receives usdcE without having transferred any USDC.

Contrast this with the rest of the codebase: `ERC20Helper.safeTransfer` and `ERC20Helper.safeTransferFrom` both check the return value and revert on failure. [1](#0-0) 

The `replaceUsdcEWithUsdc` function does not use these safe wrappers for the inbound USDC pull. [2](#0-1) 

---

### Impact Explanation

An attacker on chain 57073 (Ink) can drain all usdcE held in any DDA (`directDepositV1Address[subaccount]`) without providing USDC, by calling `replaceUsdcEWithUsdc` with zero USDC allowance (or with a USDC token that returns `false` on failure). The corrupted asset delta is: usdcE balance of the DDA is zeroed, and the attacker's usdcE balance increases by `balance`, with no corresponding USDC transfer occurring.

---

### Likelihood Explanation

The function is callable by any unprivileged address on chain 57073 — no `onlyOwner` or similar guard is present. [3](#0-2) 

Whether the USDC token at `0x2D270e6886d130D724215A266106e6832161EAEd` returns `false` or reverts on failure determines exploitability. Bridged or non-standard USDC variants (common on newer chains like Ink) may return `false`. Even if the current deployment reverts, the code is structurally broken and a future token upgrade or redeployment could make it exploitable.

---

### Recommendation

Replace the raw `transferFrom` call with `safeTransferFrom` from `ERC20Helper`, consistent with how all other token transfers in the protocol are handled:

```solidity
// Before (vulnerable):
IERC20Base(usdc).transferFrom(msg.sender, directDepositV1, balance);

// After (safe):
IERC20Base(usdc).safeTransferFrom(msg.sender, directDepositV1, balance);
``` [1](#0-0) 

---

### Proof of Concept

1. A DDA exists for some `subaccount` on chain 57073 and holds `N` usdcE tokens.
2. Attacker calls `ContractOwner.replaceUsdcEWithUsdc(subaccount)` with zero USDC allowance.
3. `IERC20Base(usdc).transferFrom(attacker, directDepositV1, N)` returns `false` — no USDC moves.
4. `DirectDepositV1(directDepositV1).withdraw(usdcE)` executes — `N` usdcE moves from DDA to `ContractOwner`.
5. `IERC20Base(usdcE).safeTransfer(attacker, N)` executes — `N` usdcE moves from `ContractOwner` to attacker.
6. Attacker holds `N` usdcE; no USDC was provided. [4](#0-3)

### Citations

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
